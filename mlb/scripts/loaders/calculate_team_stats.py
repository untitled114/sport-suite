#!/usr/bin/env python3
"""
Calculate MLB Team Season + Rolling Stats from Games Data
=========================================================
Derives team-level aggregates from ``mlb_games.games`` final scores and writes:
    - mlb_teams.team_season_stats   (per-season runs scored / allowed, run diff)
    - mlb_teams.team_rolling_stats  (last-N-game form per team)

Mirrors ``nba/scripts/loaders/calculate_team_stats.py`` (runs replace points;
no pace/possessions in baseball).

Usage:
    # Default: current season
    python -m mlb.scripts.loaders.calculate_team_stats

    # Specific seasons (the year embedded in game_date)
    python -m mlb.scripts.loaders.calculate_team_stats --season 2023 2024
"""

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import psycopg2
from psycopg2.extras import execute_values

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from mlb.config.database import get_games_db_config, get_team_db_config
from mlb.sql import named_query

EST = ZoneInfo("America/New_York")

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

GAMES_DB = get_games_db_config()
TEAM_DB = get_team_db_config()

ROLLING_WINDOW = 10


def calculate_team_season_stats(seasons: list) -> int:
    """Aggregate per-team runs scored/allowed for each season from games scores."""
    games_conn = psycopg2.connect(**GAMES_DB)
    team_conn = psycopg2.connect(**TEAM_DB)
    total = 0

    try:
        for season in seasons:
            logger.info(f"Calculating team season stats for {season}...")
            cur = games_conn.cursor()

            # One row per (team, game) from both home and away perspectives,
            # then aggregate runs scored / allowed per team for the season.
            cur.execute(
                named_query("team_season_runs"),
                (season, season),
            )
            rows = cur.fetchall()
            cur.close()

            if not rows:
                logger.warning(f"No completed games for season {season}")
                continue

            insert_data = []
            for team, games, rs, ra, wins in rows:
                rs = float(rs)
                ra = float(ra)
                insert_data.append(
                    (
                        team,
                        season,
                        games,
                        round(rs, 3),
                        round(ra, 3),
                        round(rs - ra, 3),
                        round(wins / games, 3) if games else None,
                    )
                )

            team_cur = team_conn.cursor()
            execute_values(
                team_cur,
                """
                INSERT INTO team_season_stats
                (team_abbrev, season, games, avg_runs_scored, avg_runs_allowed,
                 run_differential, win_pct)
                VALUES %s
                ON CONFLICT (team_abbrev, season)
                DO UPDATE SET
                    games = EXCLUDED.games,
                    avg_runs_scored = EXCLUDED.avg_runs_scored,
                    avg_runs_allowed = EXCLUDED.avg_runs_allowed,
                    run_differential = EXCLUDED.run_differential,
                    win_pct = EXCLUDED.win_pct
                """,
                insert_data,
            )
            team_conn.commit()
            team_cur.close()
            total += len(insert_data)
            logger.info(f"OK {len(insert_data)} team season stats for {season}")

        return total

    except (psycopg2.Error, KeyError, TypeError, ValueError) as e:
        logger.error(f"Error calculating season stats: {e}")
        raise
    finally:
        games_conn.close()
        team_conn.close()


def calculate_team_rolling_stats(seasons: list, window: int = ROLLING_WINDOW) -> int:
    """Compute last-N-game rolling runs scored/allowed per team (most recent game)."""
    games_conn = psycopg2.connect(**GAMES_DB)
    team_conn = psycopg2.connect(**TEAM_DB)
    total = 0

    try:
        cur = games_conn.cursor()
        cur.execute(
            named_query("team_rolling_runs"),
            (seasons, seasons, window),
        )
        rows = cur.fetchall()
        cur.close()

        if not rows:
            logger.warning("No games available for rolling stats")
            return 0

        insert_data = [
            (
                team,
                window,
                games,
                round(float(rs), 3),
                round(float(ra), 3),
                as_of,
            )
            for team, games, rs, ra, as_of in rows
        ]

        team_cur = team_conn.cursor()
        execute_values(
            team_cur,
            """
            INSERT INTO team_rolling_stats
            (team_abbrev, window_size, games, avg_runs_scored, avg_runs_allowed, as_of_date)
            VALUES %s
            ON CONFLICT (team_abbrev, window_size)
            DO UPDATE SET
                games = EXCLUDED.games,
                avg_runs_scored = EXCLUDED.avg_runs_scored,
                avg_runs_allowed = EXCLUDED.avg_runs_allowed,
                as_of_date = EXCLUDED.as_of_date
            """,
            insert_data,
        )
        team_conn.commit()
        team_cur.close()
        total = len(insert_data)
        logger.info(f"OK {total} team rolling stats (window={window})")
        return total

    except (psycopg2.Error, KeyError, TypeError, ValueError) as e:
        logger.error(f"Error calculating rolling stats: {e}")
        raise
    finally:
        games_conn.close()
        team_conn.close()


def _default_seasons() -> list:
    """Default to the current MLB season year (EST)."""
    return [datetime.now(EST).year]


def main():
    parser = argparse.ArgumentParser(description="Calculate MLB team stats from games data")
    parser.add_argument(
        "--season",
        type=int,
        nargs="+",
        default=None,
        help="Season years (calendar year of game_date). Defaults to current season.",
    )
    parser.add_argument(
        "--window", type=int, default=ROLLING_WINDOW, help="Rolling window size (games)"
    )
    args = parser.parse_args()

    seasons = args.season or _default_seasons()
    logger.info(f"Calculating team stats for seasons {seasons}...")
    calculate_team_season_stats(seasons)
    calculate_team_rolling_stats(seasons, window=args.window)
    logger.info("Team stats calculation complete!")


if __name__ == "__main__":
    main()
