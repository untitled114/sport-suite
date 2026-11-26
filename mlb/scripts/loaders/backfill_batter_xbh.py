#!/usr/bin/env python3
"""
Batter Extra-Base-Hit + wOBA-Component Backfill Loader
======================================================
Backfills the TRUE-wOBA component columns on ``mlb_players.batter_game_logs``
(added by ``mlb/db/schema_xbh.sql``):

    doubles, triples, hit_by_pitch, intentional_walks, sac_flies

from the MLB Stats API hitting gameLog ``stat`` payload: the same
``/people/{id}/stats?stats=gameLog&group=hitting`` endpoint that
``load_batter_gamelogs.py`` reads, which does not store these fields.

These columns let the sabermetric extractor compute wOBA:
    1B   = H - 2B - 3B - HR                       (singles)
    uBB  = BB - IBB                               (unintentional walks)
    wOBA = (wBB*uBB + wHBP*HBP + w1B*1B + w2B*2B + w3B*3B + wHR*HR)
           / (AB + BB - IBB + SF + HBP)
instead of a guessed OBP/SLG blend.

This loader is UPDATE-only, matched by ``(player_id, game_id)``; it never mutates
any other column and never defaults a missing value (rows lacking a parseable
gamePk are skipped).

Usage:
    # Current season, all batters in batter_profile
    python -m mlb.scripts.loaders.backfill_batter_xbh

    # Specific seasons
    python -m mlb.scripts.loaders.backfill_batter_xbh --season 2024 2025 2026

    # Single batter
    python -m mlb.scripts.loaders.backfill_batter_xbh --player-id 660271 --season 2024
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
    """Coerce a stat value to int, or None if absent/unparseable (never defaulted)."""
    if value is None:
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def _xbh_updates_for_player(
    api: StatsAPIWrapper, player_id: int, seasons: List[int]
) -> List[Tuple[int, int, int, int, int, int, int]]:
    """
    Build wOBA-component update tuples for a batter.

    Each tuple is
    ``(doubles, triples, hit_by_pitch, intentional_walks, sac_flies, player_id, game_id)``
    ready for the UPDATE below. The five component counts default to 0 when the
    API omits them (a clean box-score line legitimately has 0 doubles, etc.), but
    a split with NO parseable gamePk is skipped entirely - we never invent a game.
    """
    updates: List[Tuple[int, int, int, int, int, int, int]] = []
    for season in seasons:
        try:
            splits = api.get_player_game_log(player_id, "hitting", season)
        except (KeyError, TypeError, ValueError) as e:
            logger.warning(f"  fetch failed for {player_id} season {season}: {e}")
            continue
        for split in splits:
            stat = split.get("stat", {})
            game = split.get("game", {})
            game_pk = _int(game.get("gamePk"))
            if game_pk is None:
                continue
            updates.append(
                (
                    _int(stat.get("doubles")) or 0,
                    _int(stat.get("triples")) or 0,
                    _int(stat.get("hitByPitch")) or 0,
                    _int(stat.get("intentionalWalks")) or 0,
                    _int(stat.get("sacFlies")) or 0,
                    player_id,
                    game_pk,
                )
            )
    return updates


def backfill_batter_xbh(seasons: List[int], player_id: Optional[int] = None) -> int:
    """
    Backfill doubles/triples/HBP/IBB/SF for the given seasons.

    Args:
        seasons: List of 4-digit season years.
        player_id: Optional single batter; otherwise all in batter_profile.

    Returns:
        Total rows UPDATEd (game logs that received wOBA-component values).
    """
    api = StatsAPIWrapper()
    conn = psycopg2.connect(**DB_CONFIG)

    try:
        cur = conn.cursor()
        if player_id is not None:
            cur.execute(
                "SELECT player_id, full_name FROM batter_profile WHERE player_id = %s",
                (player_id,),
            )
        else:
            cur.execute("SELECT player_id, full_name FROM batter_profile")
        targets = cur.fetchall()
        cur.close()

        logger.info(f"Backfilling wOBA components for {len(targets)} batters, seasons {seasons}")

        total = 0
        for idx, (pid, full_name) in enumerate(targets, 1):
            updates = _xbh_updates_for_player(api, pid, seasons)
            if not updates:
                continue

            cur = conn.cursor()
            try:
                cur.executemany(
                    """
                    UPDATE batter_game_logs
                       SET doubles = %s,
                           triples = %s,
                           hit_by_pitch = %s,
                           intentional_walks = %s,
                           sac_flies = %s
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

        logger.info(f"Backfilled wOBA components on {total} batter game logs")
        return total

    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(
        description="Backfill MLB batter wOBA components (2B/3B/HBP/IBB/SF)"
    )
    parser.add_argument("--season", type=int, nargs="+", default=None, help="Season years")
    parser.add_argument("--player-id", type=int, default=None, help="Single batter id")
    args = parser.parse_args()

    seasons = args.season or [get_current_season()]
    try:
        backfill_batter_xbh(seasons, player_id=args.player_id)
    except (psycopg2.Error, KeyError, TypeError, ValueError) as e:
        logger.error(f"Backfill failed: {e}")
        raise


if __name__ == "__main__":
    main()
