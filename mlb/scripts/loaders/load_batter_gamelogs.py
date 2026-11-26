#!/usr/bin/env python3
"""
Batter Game Logs Loader
=======================
Loads per-game hitting logs into ``mlb_players.batter_game_logs`` for every
batter in ``mlb_players.batter_profile`` (or a single player), for one or more
seasons. Mirrors ``nba/scripts/loaders/load_player_gamelogs_bulk.py`` adapted to
the MLB Stats API ``gameLog`` endpoint.

Feeds the HITS and TOTAL_BASES features.

Usage:
    # Current season, all batters in profile
    python -m mlb.scripts.loaders.load_batter_gamelogs

    # Specific seasons
    python -m mlb.scripts.loaders.load_batter_gamelogs --season 2024 2025

    # Single batter
    python -m mlb.scripts.loaders.load_batter_gamelogs --player-id 677951 --season 2024
"""

import argparse
import logging
import sys
from pathlib import Path
from typing import List, Optional
from zoneinfo import ZoneInfo

import psycopg2
from psycopg2.extras import execute_values

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from mlb.config.database import get_players_db_config
from mlb.scripts.utilities.statsapi_wrapper import StatsAPIWrapper, get_current_season

EST = ZoneInfo("America/New_York")

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

DB_CONFIG = get_players_db_config()


def _int(v) -> int:
    """Coerce a stat value to int, defaulting to 0."""
    try:
        return int(v)
    except (ValueError, TypeError):
        return 0


def _parse_split(split: dict, player_id: int, full_name: str, team_map: dict):
    """Parse one gameLog split into a batter_game_logs insert tuple."""
    stat = split.get("stat", {})
    game = split.get("game", {})

    team = split.get("team", {})
    opp = split.get("opponent", {})
    team_abbrev = team.get("abbreviation") or team_map.get(team.get("id"))
    opp_abbrev = opp.get("abbreviation") or team_map.get(opp.get("id"))
    home_away = "home" if split.get("isHome") else "away"

    return (
        player_id,
        full_name,
        team_abbrev,
        game.get("gamePk"),
        split.get("date"),
        opp_abbrev,
        home_away,
        _int(stat.get("atBats")),
        _int(stat.get("hits")),
        _int(stat.get("totalBases")),
        _int(stat.get("runs")),
        _int(stat.get("rbi")),
        _int(stat.get("homeRuns")),
        _int(stat.get("baseOnBalls")),
        _int(stat.get("strikeOuts")),
        _int(stat.get("plateAppearances")),
    )


def load_batter_gamelogs(seasons: List[int], player_id: Optional[int] = None) -> int:
    """
    Load hitting game logs for the given seasons.

    Args:
        seasons: List of 4-digit season years.
        player_id: Optional single batter; otherwise all in batter_profile.

    Returns:
        Total rows upserted.
    """
    api = StatsAPIWrapper()
    conn = psycopg2.connect(**DB_CONFIG)
    team_map = api.get_team_id_map()

    try:
        if player_id is not None:
            cur = conn.cursor()
            cur.execute("SELECT full_name FROM batter_profile WHERE player_id = %s", (player_id,))
            row = cur.fetchone()
            cur.close()
            targets = [(player_id, row[0] if row else None)]
        else:
            cur = conn.cursor()
            cur.execute("SELECT player_id, full_name FROM batter_profile")
            targets = cur.fetchall()
            cur.close()

        logger.info(f"Loading batter game logs for {len(targets)} batters, seasons {seasons}")

        total = 0
        for idx, (pid, full_name) in enumerate(targets, 1):
            rows = []
            for season in seasons:
                try:
                    splits = api.get_player_game_log(pid, "hitting", season)
                except (psycopg2.Error, KeyError, TypeError, ValueError) as e:
                    logger.warning(
                        f"  FAILED {full_name} ({pid}) season {season} fetch failed: {e}"
                    )
                    continue
                for split in splits:
                    rows.append(_parse_split(split, pid, full_name, team_map))

            if not rows:
                continue

            cur = conn.cursor()
            try:
                execute_values(
                    cur,
                    """
                    INSERT INTO batter_game_logs
                    (player_id, full_name, team, game_id, game_date, opponent, home_away,
                     at_bats, hits, total_bases, runs, rbis, home_runs, walks,
                     strikeouts, plate_appearances)
                    VALUES %s
                    ON CONFLICT (player_id, game_id)
                    DO UPDATE SET
                        at_bats = EXCLUDED.at_bats,
                        hits = EXCLUDED.hits,
                        total_bases = EXCLUDED.total_bases,
                        runs = EXCLUDED.runs,
                        rbis = EXCLUDED.rbis,
                        home_runs = EXCLUDED.home_runs,
                        walks = EXCLUDED.walks,
                        strikeouts = EXCLUDED.strikeouts,
                        plate_appearances = EXCLUDED.plate_appearances
                    """,
                    rows,
                )
                conn.commit()
                total += len(rows)
                logger.info(f"[{idx}/{len(targets)}] {full_name}: {len(rows)} logs")
            except (psycopg2.Error, KeyError, TypeError, ValueError) as e:
                conn.rollback()
                logger.error(f"  insert failed for {full_name}: {e}")
            finally:
                cur.close()

        logger.info(f"Loaded {total} batter game logs")
        return total

    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description="Load MLB batter game logs")
    parser.add_argument("--season", type=int, nargs="+", default=None, help="Season years")
    parser.add_argument("--player-id", type=int, default=None, help="Single batter id")
    args = parser.parse_args()

    seasons = args.season or [get_current_season()]
    try:
        load_batter_gamelogs(seasons, player_id=args.player_id)
    except (psycopg2.Error, KeyError, TypeError, ValueError) as e:
        logger.error(f"Loading failed: {e}")
        raise


if __name__ == "__main__":
    main()
