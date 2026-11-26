#!/usr/bin/env python3
"""
Pitcher Game Logs Loader
========================
Loads per-game pitching logs into ``mlb_players.pitcher_game_logs`` for every
pitcher in ``mlb_players.pitcher_profile`` (or a single player), for one or more
seasons. Mirrors ``nba/scripts/loaders/load_player_gamelogs_bulk.py`` adapted to
the MLB Stats API ``gameLog`` endpoint.

These logs feed the STRIKEOUTS features.

Usage:
    # Current season, all pitchers in profile
    python -m mlb.scripts.loaders.load_pitcher_gamelogs

    # Specific seasons
    python -m mlb.scripts.loaders.load_pitcher_gamelogs --season 2024 2025

    # Single pitcher
    python -m mlb.scripts.loaders.load_pitcher_gamelogs --player-id 684320 --season 2024
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


def _innings_to_float(ip) -> Optional[float]:
    """Convert MLB innings-pitched notation ('5.2' = 5 + 2/3) to a true float."""
    if ip is None:
        return None
    try:
        whole, _, frac = str(ip).partition(".")
        whole = int(whole) if whole else 0
        outs = int(frac) if frac else 0
        return whole + outs / 3.0
    except (ValueError, TypeError):
        return None


def _int(v) -> int:
    """Coerce a stat value to int, defaulting to 0."""
    try:
        return int(v)
    except (ValueError, TypeError):
        return 0


def _profile_player_ids(conn) -> List[int]:
    """Read all pitcher ids from pitcher_profile."""
    cur = conn.cursor()
    cur.execute("SELECT player_id FROM pitcher_profile")
    ids = [row[0] for row in cur.fetchall()]
    cur.close()
    return ids


def _parse_split(split: dict, player_id: int, full_name: str, team_map: dict):
    """Parse one gameLog split into a pitcher_game_logs insert tuple."""
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
        _innings_to_float(stat.get("inningsPitched")),
        _int(stat.get("strikeOuts")),
        _int(stat.get("battersFaced")),
        _int(stat.get("numberOfPitches")),
        _int(stat.get("earnedRuns")),
        _int(stat.get("hits")),
        _int(stat.get("baseOnBalls")),
    )


def load_pitcher_gamelogs(seasons: List[int], player_id: Optional[int] = None) -> int:
    """
    Load pitching game logs for the given seasons.

    Args:
        seasons: List of 4-digit season years.
        player_id: Optional single pitcher; otherwise all in pitcher_profile.

    Returns:
        Total rows upserted.
    """
    api = StatsAPIWrapper()
    conn = psycopg2.connect(**DB_CONFIG)
    team_map = api.get_team_id_map()

    try:
        if player_id is not None:
            cur = conn.cursor()
            cur.execute("SELECT full_name FROM pitcher_profile WHERE player_id = %s", (player_id,))
            row = cur.fetchone()
            cur.close()
            targets = [(player_id, row[0] if row else None)]
        else:
            cur = conn.cursor()
            cur.execute("SELECT player_id, full_name FROM pitcher_profile")
            targets = cur.fetchall()
            cur.close()

        logger.info(f"Loading pitcher game logs for {len(targets)} pitchers, seasons {seasons}")

        total = 0
        for idx, (pid, full_name) in enumerate(targets, 1):
            rows = []
            for season in seasons:
                try:
                    splits = api.get_player_game_log(pid, "pitching", season)
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
                    INSERT INTO pitcher_game_logs
                    (player_id, full_name, team, game_id, game_date, opponent, home_away,
                     innings_pitched, strikeouts, batters_faced, pitches, earned_runs,
                     hits_allowed, walks)
                    VALUES %s
                    ON CONFLICT (player_id, game_id)
                    DO UPDATE SET
                        innings_pitched = EXCLUDED.innings_pitched,
                        strikeouts = EXCLUDED.strikeouts,
                        batters_faced = EXCLUDED.batters_faced,
                        pitches = EXCLUDED.pitches,
                        earned_runs = EXCLUDED.earned_runs,
                        hits_allowed = EXCLUDED.hits_allowed,
                        walks = EXCLUDED.walks
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

        logger.info(f"Loaded {total} pitcher game logs")
        return total

    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description="Load MLB pitcher game logs")
    parser.add_argument("--season", type=int, nargs="+", default=None, help="Season years")
    parser.add_argument("--player-id", type=int, default=None, help="Single pitcher id")
    args = parser.parse_args()

    seasons = args.season or [get_current_season()]
    try:
        load_pitcher_gamelogs(seasons, player_id=args.player_id)
    except (psycopg2.Error, KeyError, TypeError, ValueError) as e:
        logger.error(f"Loading failed: {e}")
        raise


if __name__ == "__main__":
    main()
