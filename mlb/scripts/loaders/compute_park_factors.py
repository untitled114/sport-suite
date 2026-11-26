#!/usr/bin/env python3
"""
Park-Factor Computation Loader
==============================
Computes MLB park factors from the stored games and persists them to
``mlb_teams.park_factors`` (created by ``mlb/db/schema_parkfactors.sql``).

Every factor is a ratio
of an observed per-game rate AT a venue to the league-wide per-game rate, over a
multi-season rolling window.

Computation
-----------
Population: regular-/post-season games we already store in ``mlb_games.games``
with ``LOWER(status) IN ('final', 'completed early')`` and a non-NULL home score,
within ``[START_SEASON, END_SEASON]``. Home-team codes are normalized to the
canonical 3-letter abbreviation and restricted to the 30 real MLB clubs
(``VALID_TEAMS``) so exhibition / neutral-site rows (All-Star, Mexico City,
spring training) never pollute a park's rate.

For each park P (= home team's venue):
    n_games(P)        = # qualifying games played at P
    runs/game(P)      = SUM(home_score + away_score at P) / n_games(P)
    hr/game(P)        = SUM(batter home_runs in P's games) / n_games(P)
    hits/game(P)      = SUM(batter hits      in P's games) / n_games(P)

League baselines are the SAME rates pooled over EVERY qualifying game:
    lg_runs/game = SUM(home_score+away_score) / N
    lg_hr/game   = SUM(home_runs) / N
    lg_hits/game = SUM(hits)      / N

Factors (1.0 = league-neutral):
    runs_factor(P)  = (runs/game(P))  / (lg_runs/game)
    hr_factor(P)    = (hr/game(P))    / (lg_hr/game)
    hits_factor(P)  = (hits/game(P))  / (lg_hits/game)

The batter HR/hits totals come from ``mlb_players.batter_game_logs`` joined to
``mlb_games.games`` by ``game_id`` (both teams' batting lines are counted, which
is the correct "what happens at this park" view). Runs come straight from the
game scores.

Persistence: one row per (home_team, window_label) UPSERTed into
``mlb_teams.park_factors`` with ``n_games`` for sample-size gating downstream.

Usage:
    # Default rolling window (current + prior seasons)
    python -m mlb.scripts.loaders.compute_park_factors

    # Explicit seasons
    python -m mlb.scripts.loaders.compute_park_factors --start 2024 --end 2026
"""

import argparse
import logging
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import psycopg2
from psycopg2.extras import execute_values

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from mlb.config.database import get_team_db_config
from mlb.scripts.utilities.statsapi_wrapper import get_current_season
from mlb.sql import named_query
from mlb.utils.team_abbrev import VALID_TEAMS, normalize_team_abbrev

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

DB_CONFIG = get_team_db_config()

# Games we trust as a completed competitive contest.
FINAL_STATUSES = ("final", "completed early")

# Minimum qualifying games for a park's factor to be considered reliable. Below
# this the reader treats the factor as unknown (neutral fallback + *_known=0).
MIN_GAMES = 30


def _fetch_runs_rows(conn, start: int, end: int) -> List[Tuple[str, int, int]]:
    """
    Return per-game (home_team_raw, total_runs, 1) rows for qualifying games.

    Each row is one game so the caller can aggregate runs and game counts per
    park and league-wide from a single pull.
    """
    cur = conn.cursor()
    cur.execute(
        """
        SELECT home_team,
               (COALESCE(home_score, 0) + COALESCE(away_score, 0)) AS total_runs
          FROM mlb_games.games
         WHERE LOWER(status) = ANY(%s)
           AND home_score IS NOT NULL
           AND EXTRACT(YEAR FROM game_date)::int BETWEEN %s AND %s
        """,
        (list(FINAL_STATUSES), start, end),
    )
    rows = [(home, int(runs), 1) for home, runs in cur.fetchall()]
    cur.close()
    return rows


def _fetch_batting_rows(conn, start: int, end: int) -> List[Tuple[str, int, int]]:
    """
    Return per-game (home_team_raw, sum_home_runs, sum_hits) rows.

    Aggregates BOTH teams' batting lines (every batter_game_logs row for the game)
    grouped by the game's venue (home team), so the totals describe production AT
    that park.
    """
    cur = conn.cursor()
    cur.execute(
        named_query("home_park_batting_per_game"),
        (list(FINAL_STATUSES), start, end),
    )
    rows = [(home, int(hr), int(hits)) for home, hr, hits in cur.fetchall()]
    cur.close()
    return rows


def _aggregate(
    runs_rows: List[Tuple[str, int, int]],
    bat_rows: List[Tuple[str, int, int]],
) -> Dict[str, Dict[str, float]]:
    """
    Fold per-game rows into per-park totals keyed by canonical team.

    Returns ``{team: {"games", "runs", "hr", "hits"}}`` for canonical MLB teams
    only (neutral-site / exhibition rows are dropped here, not earlier, so the
    raw pulls stay simple).
    """
    parks: Dict[str, Dict[str, float]] = {}

    def _slot(team_raw: str) -> Optional[Dict[str, float]]:
        canon = normalize_team_abbrev(team_raw or "")
        if canon not in VALID_TEAMS:
            return None
        return parks.setdefault(canon, {"games": 0.0, "runs": 0.0, "hr": 0.0, "hits": 0.0})

    for home, runs, games in runs_rows:
        slot = _slot(home)
        if slot is None:
            continue
        slot["runs"] += runs
        slot["games"] += games

    for home, hr, hits in bat_rows:
        slot = _slot(home)
        if slot is None:
            continue
        slot["hr"] += hr
        slot["hits"] += hits

    return parks


def _league_rate(parks: Dict[str, Dict[str, float]], key: str) -> float:
    """League-wide per-game rate for ``key`` ('runs'|'hr'|'hits') across all parks."""
    total = sum(p[key] for p in parks.values())
    games = sum(p["games"] for p in parks.values())
    return total / games if games > 0 else 0.0


def compute_factors(
    parks: Dict[str, Dict[str, float]],
) -> Dict[str, Dict[str, float]]:
    """
    Turn per-park totals into runs/hr/hits factors (1.0 = league-neutral).

    A factor is None when the league baseline rate is 0 (degenerate / empty data)
    so the persistence layer stores NULL rather than a fabricated 1.0.
    """
    lg_runs = _league_rate(parks, "runs")
    lg_hr = _league_rate(parks, "hr")
    lg_hits = _league_rate(parks, "hits")

    out: Dict[str, Dict[str, float]] = {}
    for team, p in parks.items():
        games = p["games"]
        if games <= 0:
            continue
        runs_pg = p["runs"] / games
        hr_pg = p["hr"] / games
        hits_pg = p["hits"] / games
        out[team] = {
            "runs_factor": round(runs_pg / lg_runs, 4) if lg_runs > 0 else None,
            "hr_factor": round(hr_pg / lg_hr, 4) if lg_hr > 0 else None,
            "hits_factor": round(hits_pg / lg_hits, 4) if lg_hits > 0 else None,
            "n_games": int(games),
        }
    return out


def _persist(conn, window_label: str, factors: Dict[str, Dict[str, float]]) -> int:
    """UPSERT computed factors into mlb_teams.park_factors. Returns rows written."""
    rows = [
        (
            team,
            window_label,
            f["runs_factor"],
            f["hr_factor"],
            f["hits_factor"],
            f["n_games"],
        )
        for team, f in sorted(factors.items())
    ]
    if not rows:
        return 0
    cur = conn.cursor()
    execute_values(
        cur,
        """
        INSERT INTO mlb_teams.park_factors
            (home_team, window_label, runs_factor, hr_factor, hits_factor, n_games)
        VALUES %s
        ON CONFLICT (home_team, window_label) DO UPDATE SET
            runs_factor = EXCLUDED.runs_factor,
            hr_factor   = EXCLUDED.hr_factor,
            hits_factor = EXCLUDED.hits_factor,
            n_games     = EXCLUDED.n_games,
            computed_at = now()
        """,
        rows,
    )
    conn.commit()
    cur.close()
    return len(rows)


def compute_park_factors(start: int, end: int) -> Dict[str, Dict[str, float]]:
    """
    Compute and persist park factors for the rolling window ``[start, end]``.

    Returns the ``{team: {runs_factor, hr_factor, hits_factor, n_games}}`` mapping
    that was written (also persisted to ``mlb_teams.park_factors``).
    """
    window_label = f"rolling_{start}_{end}"
    conn = psycopg2.connect(**DB_CONFIG)
    try:
        runs_rows = _fetch_runs_rows(conn, start, end)
        bat_rows = _fetch_batting_rows(conn, start, end)
        parks = _aggregate(runs_rows, bat_rows)
        factors = compute_factors(parks)
        written = _persist(conn, window_label, factors)
        logger.info(
            "Computed park factors for %d parks (window %s, %d games total)",
            written,
            window_label,
            sum(int(p["games"]) for p in parks.values()),
        )
        for team in sorted(factors):
            f = factors[team]
            logger.info(
                "  %s: runs=%s hr=%s hits=%s (n=%d)",
                team,
                f["runs_factor"],
                f["hr_factor"],
                f["hits_factor"],
                f["n_games"],
            )
        return factors
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description="Compute MLB park factors from real data")
    parser.add_argument("--start", type=int, default=None, help="First season (default current-2)")
    parser.add_argument("--end", type=int, default=None, help="Last season (default current)")
    args = parser.parse_args()

    end = args.end or get_current_season()
    start = args.start or (end - 2)
    try:
        compute_park_factors(start, end)
    except (psycopg2.Error, KeyError, TypeError, ValueError) as e:
        logger.error(f"Park-factor computation failed: {e}")
        raise


if __name__ == "__main__":
    main()
