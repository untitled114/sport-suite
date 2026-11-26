#!/usr/bin/env python3
"""
NBA Games Data Loader (Incremental)
====================================
Fast incremental loader for daily pipeline.
Only processes games newer than the latest in database.

Uses LeagueGameFinder which returns ALL season games in ONE API call,
then filters to only insert new records.

Usage:
    # Daily pipeline (auto-detects season)
    python load_nba_games_incremental.py

    # Specific season
    python load_nba_games_incremental.py --season 2025-26
"""

import argparse
import logging
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import psycopg2
from psycopg2.extras import execute_values

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from nba.config.database import get_games_db_config
from nba.utils.season import current_nba_season

# Add utilities to path
sys.path.append(os.path.join(os.path.dirname(__file__), "..", "utilities"))
from zoneinfo import ZoneInfo

from nba_api_wrapper import NBAApiWrapper

EST = ZoneInfo("America/New_York")

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# Database connection params
DB_CONFIG = get_games_db_config()

# Every kind of game a season has. The regular season alone left the play-in and playoffs unloaded.
SEASON_TYPES = ("Regular Season", "PlayIn", "Playoffs")


def get_load_start(conn, season_year: int, today=None) -> str:
    """The date after which the season's games are (re)loaded, YYYY-MM-DD.

    The day before the season's earliest past game that still has no scores; with none, the
    latest scored game. A row without scores never counts as loaded: the schedule holds rows for
    upcoming games, and were MAX(game_date) to count them every run would start after a future
    date and load nothing.
    """
    today = today or datetime.now(EST).date()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT (SELECT MIN(game_date) FROM games
                 WHERE season = %s AND game_date < %s
                   AND (home_score IS NULL OR away_score IS NULL)),
               (SELECT MAX(game_date) FROM games
                 WHERE season = %s AND home_score IS NOT NULL AND away_score IS NOT NULL)
        """,
        (season_year, today, season_year),
    )
    unscored, scored = cursor.fetchone()
    cursor.close()
    if unscored:
        return (unscored - timedelta(days=1)).strftime("%Y-%m-%d")
    if scored:
        return scored.strftime("%Y-%m-%d")
    return "2000-01-01"  # Nothing loaded for the season yet: load everything


def adopt_existing_ids(games_rows: list, log_rows: list, existing: dict) -> int:
    """Give each game the id its row already has, so the upsert fills that row.

    `existing` maps (game_date, home_team, away_team) to the stored game_id. A game a schedule
    fetcher stored first keeps its placeholder id, for its games row and its team-log rows alike:
    team_game_logs references games.game_id, and (game_date, home_team, away_team) is unique.
    Returns how many games took a stored id.
    """
    remap = {}
    for game in games_rows:
        key = (pd.Timestamp(game["game_date"]).date(), game["home_team"], game["away_team"])
        stored = existing.get(key)
        if stored and stored != game["game_id"]:
            remap[game["game_id"]] = stored
            game["game_id"] = stored
    for log in log_rows:
        if log["game_id"] in remap:
            log["game_id"] = remap[log["game_id"]]
    return len(remap)


BOX_COLUMNS = {
    "fg_made": "FGM",
    "fg_attempted": "FGA",
    "three_pt_made": "FG3M",
    "three_pt_attempted": "FG3A",
    "ft_made": "FTM",
    "ft_attempted": "FTA",
    "rebounds": "REB",
    "assists": "AST",
    "turnovers": "TOV",
}


def _int_or_none(value):
    return None if value is None or pd.isna(value) else int(value)


def build_game_rows(games_df: pd.DataFrame, season_year: int, after_date: str):
    """Rows for games and team_game_logs from LeagueGameFinder (one row per team per game).

    Each game gets one games row with BOTH scores and two team-log rows with full box
    stats. Games on or before ``after_date`` (YYYY-MM-DD) are skipped. Games missing either
    team's row are skipped rather than written half-filled.
    """
    games, logs = [], []
    df = games_df.copy()
    df["GAME_DATE"] = pd.to_datetime(df["GAME_DATE"])
    df = df[df["GAME_DATE"] > pd.Timestamp(after_date)]
    for game_id, rows in df.groupby("GAME_ID", sort=True):
        home = rows[rows["MATCHUP"].str.contains("vs.", regex=False)]
        away = rows[rows["MATCHUP"].str.contains("@", regex=False)]
        if len(home) == 0 and len(away) == 2:
            # Neutral site (e.g. the 2026 Berlin and London games): both rows read
            # "AWAY @ HOME", so the designated home team is the one after the "@".
            home_abbrev = away["MATCHUP"].iloc[0].split("@")[-1].strip()
            home = rows[rows["TEAM_ABBREVIATION"] == home_abbrev]
            away = rows[rows["TEAM_ABBREVIATION"] != home_abbrev]
        if len(home) != 1 or len(away) != 1:
            logger.warning(f"Skipping {game_id}: expected one home and one away row")
            continue
        home, away = home.iloc[0], away.iloc[0]
        game_date = home["GAME_DATE"]
        games.append(
            {
                "game_id": game_id,
                "game_date": game_date,
                "season": season_year,
                "season_type": home.get("SEASON_TYPE"),
                "home_team": home["TEAM_ABBREVIATION"],
                "away_team": away["TEAM_ABBREVIATION"],
                "home_score": _int_or_none(home["PTS"]),
                "away_score": _int_or_none(away["PTS"]),
            }
        )
        for team, opp, is_home in ((home, away, True), (away, home, False)):
            log = {
                "team_abbrev": team["TEAM_ABBREVIATION"],
                "game_id": game_id,
                "game_date": game_date,
                "season": season_year,
                "opponent": opp["TEAM_ABBREVIATION"],
                "is_home": is_home,
                "points": _int_or_none(team["PTS"]),
            }
            for col, src in BOX_COLUMNS.items():
                log[col] = _int_or_none(team[src])
            logs.append(log)
    return games, logs


def load_games_incremental(season: str = None, since: str = None) -> int:
    """
    Load only NEW games since last update.

    Returns:
        Number of new games loaded
    """
    if season is None:
        season = current_nba_season()

    logger.info(f"=== Incremental Games Loader for {season} ===")

    conn = psycopg2.connect(**DB_CONFIG)

    try:
        season_year = int(season.split("-")[0]) + 1  # 2025-26 -> 2026
        latest_date = get_load_start(conn, season_year)
        logger.info(f"Loading {season} games after {latest_date}")
        if since:
            # Re-upsert games after `since` (fills scores/box stats on already-loaded games).
            latest_date = since
            logger.info(f"Re-loading games after {since}")

        # One call per season type (regular season, play-in, playoffs)
        api = NBAApiWrapper(requests_per_minute=20)
        frames = []
        for season_type in SEASON_TYPES:
            df = api.get_season_games(season=season, season_type=season_type)
            logger.info(f"{season_type}: {len(df)} team rows")
            if not df.empty:
                frames.append(df.assign(SEASON_TYPE=season_type))
        games_df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()

        if games_df.empty:
            logger.warning(f"No games found for {season}")
            return 0

        # Deduplicate (API returns one row per team)
        games_unique = games_df.drop_duplicates(subset=["GAME_ID"]).copy()
        logger.info(f"API returned {len(games_unique)} unique games for season")

        # Filter to only new games
        games_unique["GAME_DATE"] = pd.to_datetime(games_unique["GAME_DATE"])
        new_games = games_unique[games_unique["GAME_DATE"] > latest_date]

        if len(new_games) == 0:
            logger.info("Database is up-to-date, no new games to load")
            return 0

        logger.info(f"Found {len(new_games)} new games to load")

        games_rows, log_rows = build_game_rows(games_df, season_year, latest_date)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT game_date, home_team, away_team, game_id FROM games WHERE game_date > %s",
            (latest_date,),
        )
        existing = {(d, h, a): gid for d, h, a, gid in cursor.fetchall()}
        adopted = adopt_existing_ids(games_rows, log_rows, existing)
        if adopted:
            logger.info(f"{adopted} games fill rows stored earlier (schedule placeholders)")

        if games_rows:
            game_cols = [
                "game_id",
                "game_date",
                "season",
                "season_type",
                "home_team",
                "away_team",
                "home_score",
                "away_score",
            ]
            execute_values(
                cursor,
                f"""
                INSERT INTO games ({", ".join(game_cols)})
                VALUES %s
                ON CONFLICT (game_id)
                DO UPDATE SET
                    home_score = COALESCE(EXCLUDED.home_score, games.home_score),
                    away_score = COALESCE(EXCLUDED.away_score, games.away_score),
                    season_type = COALESCE(EXCLUDED.season_type, games.season_type)
                """,
                [tuple(r[c] for c in game_cols) for r in games_rows],
            )
            logger.info(f"Inserted {len(games_rows)} games")

        if log_rows:
            log_cols = [
                "team_abbrev",
                "game_id",
                "game_date",
                "season",
                "opponent",
                "is_home",
                "points",
                *BOX_COLUMNS,
            ]
            updates = ",\n                    ".join(
                f"{c} = EXCLUDED.{c}" for c in ["season", "points", *BOX_COLUMNS]
            )
            execute_values(
                cursor,
                f"""
                INSERT INTO team_game_logs ({", ".join(log_cols)})
                VALUES %s
                ON CONFLICT (team_abbrev, game_id, game_date)
                DO UPDATE SET
                    {updates}
                """,
                [tuple(r[c] for c in log_cols) for r in log_rows],
            )
            logger.info(f"Inserted {len(log_rows)} team game logs")

        conn.commit()
        cursor.close()

        return len(new_games)

    except (psycopg2.Error, KeyError, TypeError, ValueError) as e:
        logger.error(f"Error loading games: {e}")
        conn.rollback()
        raise
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description="Incremental NBA games loader")
    parser.add_argument(
        "--season",
        type=str,
        default=None,
        help="NBA season (e.g., 2025-26). Auto-detects if not specified.",
    )
    parser.add_argument(
        "--since",
        help="Re-upsert games after this date (YYYY-MM-DD) instead of only new ones",
    )

    args = parser.parse_args()

    try:
        new_games = load_games_incremental(args.season, since=args.since)
        if new_games > 0:
            logger.info(f"Loaded {new_games} new games!")
        else:
            logger.info("No new games to load")
        return 0
    except (psycopg2.Error, KeyError, TypeError, ValueError) as e:
        logger.error(f"Failed: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
