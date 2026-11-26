#!/usr/bin/env python3
"""
Pitcher HR-Allowed Backfill Loader
==================================
Backfills ``mlb_players.pitcher_game_logs.home_runs_allowed`` (added by
``mlb/db/schema_sabermetric.sql``) from the MLB Stats API pitching gameLog
``homeRuns`` field: the same ``/people/{id}/stats?stats=gameLog&group=pitching``
endpoint that ``load_pitcher_gamelogs.py`` reads, which does not store home runs allowed.

FIP/xFIP need HR allowed; this loader closes that gap without re-fetching or
mutating any other column (it only UPDATEs ``home_runs_allowed`` on existing
rows, matched by ``(player_id, game_id)``).

Usage:
    # Current season, all pitchers in pitcher_profile
    python -m mlb.scripts.loaders.backfill_pitcher_hr_allowed

    # Specific seasons
    python -m mlb.scripts.loaders.backfill_pitcher_hr_allowed --season 2024 2025

    # Single pitcher
    python -m mlb.scripts.loaders.backfill_pitcher_hr_allowed --player-id 684320 --season 2024
"""

import argparse
import logging
import sys
from pathlib import Path
from typing import List, Optional, Tuple

import psycopg2

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from mlb.config.database import get_players_db_config
from mlb.scripts.utilities.statsapi_wrapper import StatsAPIWrapper, get_current_season

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

DB_CONFIG = get_players_db_config()


def _int(value) -> Optional[int]:
    """Coerce a stat value to int, or None if absent/unparseable."""
    if value is None:
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def _hr_updates_for_player(
    api: StatsAPIWrapper, player_id: int, seasons: List[int]
) -> List[Tuple[int, int, int]]:
    """
    Build (home_runs_allowed, player_id, game_id) update tuples for a pitcher.

    Only games with a parseable gamePk and HR value are returned (real data;
    missing values are skipped, never defaulted).
    """
    updates: List[Tuple[int, int, int]] = []
    for season in seasons:
        try:
            splits = api.get_player_game_log(player_id, "pitching", season)
        except (KeyError, TypeError, ValueError) as e:
            logger.warning(f"  fetch failed for {player_id} season {season}: {e}")
            continue
        for split in splits:
            stat = split.get("stat", {})
            game = split.get("game", {})
            game_pk = _int(game.get("gamePk"))
            hr = _int(stat.get("homeRuns"))
            if game_pk is None or hr is None:
                continue
            updates.append((hr, player_id, game_pk))
    return updates


def backfill_pitcher_hr_allowed(seasons: List[int], player_id: Optional[int] = None) -> int:
    """
    Backfill home_runs_allowed for the given seasons.

    Args:
        seasons: List of 4-digit season years.
        player_id: Optional single pitcher; otherwise all in pitcher_profile.

    Returns:
        Total rows UPDATEd (game logs that received an HR-allowed value).
    """
    api = StatsAPIWrapper()
    conn = psycopg2.connect(**DB_CONFIG)

    try:
        cur = conn.cursor()
        if player_id is not None:
            cur.execute(
                "SELECT player_id, full_name FROM pitcher_profile WHERE player_id = %s",
                (player_id,),
            )
        else:
            cur.execute("SELECT player_id, full_name FROM pitcher_profile")
        targets = cur.fetchall()
        cur.close()

        logger.info(f"Backfilling HR allowed for {len(targets)} pitchers, seasons {seasons}")

        total = 0
        for idx, (pid, full_name) in enumerate(targets, 1):
            updates = _hr_updates_for_player(api, pid, seasons)
            if not updates:
                continue

            cur = conn.cursor()
            try:
                cur.executemany(
                    """
                    UPDATE pitcher_game_logs
                       SET home_runs_allowed = %s
                     WHERE player_id = %s AND game_id = %s
                    """,
                    updates,
                )
                conn.commit()
                affected = cur.rowcount if cur.rowcount and cur.rowcount > 0 else len(updates)
                total += affected
                logger.info(f"[{idx}/{len(targets)}] {full_name}: {affected} game logs updated")
            except psycopg2.Error as e:
                conn.rollback()
                logger.error(f"  update failed for {full_name}: {e}")
            finally:
                cur.close()

        logger.info(f"Backfilled HR allowed on {total} pitcher game logs")
        return total

    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description="Backfill MLB pitcher HR allowed")
    parser.add_argument("--season", type=int, nargs="+", default=None, help="Season years")
    parser.add_argument("--player-id", type=int, default=None, help="Single pitcher id")
    args = parser.parse_args()

    seasons = args.season or [get_current_season()]
    try:
        backfill_pitcher_hr_allowed(seasons, player_id=args.player_id)
    except (psycopg2.Error, KeyError, TypeError, ValueError) as e:
        logger.error(f"Backfill failed: {e}")
        raise


if __name__ == "__main__":
    main()
