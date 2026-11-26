#!/usr/bin/env python3
"""
MLB Games Data Loader (Incremental)
===================================
Loads the schedule + final scores into ``mlb_games.games`` and probable
pitchers into ``mlb_games.probable_pitchers`` for a date range.

Mirrors ``nba/scripts/loaders/load_nba_games_incremental.py`` but uses the MLB
Stats API ``/schedule`` endpoint (one call per date) and is upsert-driven, so
re-running fills in scores for games that have since gone Final.

Usage:
    # Today only (EST)
    python -m mlb.scripts.loaders.load_mlb_games_incremental

    # Explicit range
    python -m mlb.scripts.loaders.load_mlb_games_incremental \\
        --start 2024-07-01 --end 2024-07-07
"""

import argparse
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import List
from zoneinfo import ZoneInfo

import psycopg2
from psycopg2.extras import execute_values

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from mlb.config.database import get_games_db_config
from mlb.scripts.utilities.statsapi_wrapper import StatsAPIWrapper

EST = ZoneInfo("America/New_York")

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

DB_CONFIG = get_games_db_config()

# MLB Stats API status -> normalized status string we store.
_FINAL_STATES = {"Final", "Game Over", "Completed Early"}


def _date_range(start: str, end: str) -> List[str]:
    """Inclusive list of YYYY-MM-DD strings from start to end."""
    d0 = datetime.strptime(start, "%Y-%m-%d").date()
    d1 = datetime.strptime(end, "%Y-%m-%d").date()
    out = []
    cur = d0
    while cur <= d1:
        out.append(cur.strftime("%Y-%m-%d"))
        cur += timedelta(days=1)
    return out


def _parse_game(game: dict):
    """
    Parse a schedule game dict into a games row and probable-pitcher rows.

    Returns:
        (games_row, [probable_rows]) where games_row is a tuple matching the
        insert column order, or (None, []) if the game is unusable.
    """
    game_pk = game.get("gamePk")
    if game_pk is None:
        return None, []

    # gameDate is an ISO UTC instant; convert to EST calendar date.
    game_date = game.get("officialDate")
    if not game_date and game.get("gameDate"):
        dt = datetime.fromisoformat(game["gameDate"].replace("Z", "+00:00"))
        game_date = dt.astimezone(EST).strftime("%Y-%m-%d")

    status = game.get("status", {}).get("detailedState")
    venue = game.get("venue", {}).get("name")

    teams = game.get("teams", {})
    home = teams.get("home", {})
    away = teams.get("away", {})
    home_team = home.get("team", {}).get("abbreviation")
    away_team = away.get("team", {}).get("abbreviation")

    is_final = status in _FINAL_STATES
    home_score = home.get("score") if is_final else None
    away_score = away.get("score") if is_final else None

    games_row = (
        game_pk,
        game_date,
        home_team,
        away_team,
        home_score,
        away_score,
        status,
        venue,
        None,  # park_factor: not known from the schedule; set once the venue's factor is
    )

    probable_rows = []
    for side, team_abbrev in (("home", home_team), ("away", away_team)):
        prob = teams.get(side, {}).get("probablePitcher")
        if prob and prob.get("id"):
            probable_rows.append(
                (game_pk, game_date, team_abbrev, prob["id"], prob.get("fullName"))
            )

    return games_row, probable_rows


def load_games(start: str, end: str) -> int:
    """
    Load (upsert) games + probable pitchers across an inclusive date range.

    Returns:
        Number of games upserted.
    """
    api = StatsAPIWrapper()
    conn = psycopg2.connect(**DB_CONFIG)

    games_rows = []
    probable_rows = []

    try:
        for date in _date_range(start, end):
            schedule = api.get_schedule(date)
            logger.info(f"{date}: {len(schedule)} games")
            for game in schedule:
                grow, prows = _parse_game(game)
                if grow is not None:
                    games_rows.append(grow)
                    probable_rows.extend(prows)

        # Dedupe within the batch so a single upsert never proposes two rows
        # with the same ON CONFLICT key (a game whose officialDate differs from
        # the query date can surface under two dates in one range). Keep the
        # last (most recently fetched) occurrence.
        games_rows = list({row[0]: row for row in games_rows}.values())
        probable_rows = list({(row[0], row[2]): row for row in probable_rows}.values())

        cursor = conn.cursor()

        if games_rows:
            execute_values(
                cursor,
                """
                INSERT INTO games
                (game_id, game_date, home_team, away_team, home_score, away_score,
                 status, venue, park_factor)
                VALUES %s
                ON CONFLICT (game_id)
                DO UPDATE SET
                    home_score = COALESCE(EXCLUDED.home_score, games.home_score),
                    away_score = COALESCE(EXCLUDED.away_score, games.away_score),
                    status = EXCLUDED.status,
                    venue = EXCLUDED.venue
                """,
                games_rows,
            )
            logger.info(f"Upserted {len(games_rows)} games")

        if probable_rows:
            execute_values(
                cursor,
                """
                INSERT INTO probable_pitchers
                (game_id, game_date, team, pitcher_id, pitcher_name)
                VALUES %s
                ON CONFLICT (game_id, team)
                DO UPDATE SET
                    pitcher_id = EXCLUDED.pitcher_id,
                    pitcher_name = EXCLUDED.pitcher_name
                """,
                probable_rows,
            )
            logger.info(f"Upserted {len(probable_rows)} probable pitchers")

        conn.commit()
        cursor.close()
        return len(games_rows)

    except (psycopg2.Error, KeyError, TypeError, ValueError) as e:
        logger.error(f"Error loading games: {e}")
        conn.rollback()
        raise
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description="Incremental MLB games loader")
    today = datetime.now(EST).strftime("%Y-%m-%d")
    parser.add_argument("--start", type=str, default=today, help="Start date YYYY-MM-DD (EST)")
    parser.add_argument("--end", type=str, default=None, help="End date YYYY-MM-DD (default=start)")
    args = parser.parse_args()

    end = args.end or args.start
    try:
        n = load_games(args.start, end)
        logger.info(f"Loaded {n} games ({args.start} -> {end})")
        return 0
    except (psycopg2.Error, KeyError, TypeError, ValueError) as e:
        logger.error(f"Failed: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
