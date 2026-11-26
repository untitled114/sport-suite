#!/usr/bin/env python3
"""
Batter Lineup-Slot Backfill Loader
==================================
Backfills ``mlb_players.batter_lineup_slots`` (created by
``mlb/db/schema_volume.sql``) from the MLB Stats API boxscore ``battingOrder``
field. The boxscore is the authoritative record of where each batter hit in the
order.

MLB encodes ``battingOrder`` as a 3-digit string: the leading digit(s) are the
lineup slot (1-9) and the trailing two digits flag substitution depth:
    "100" -> slot 1 (starter)
    "201" -> slot 2 (first substitute in that spot)
    "900" -> slot 9 (starter)
The lineup slot is therefore ``int(battingOrder) // 100``. Players who never
appear in the order (e.g. a pitcher in an AL boxscore) have no battingOrder and
are skipped (never defaulted).

Game ids come from ``mlb_games.games`` (status Final, case-insensitive) within the season
window, so the backfill is bounded by games we already track.

Usage:
    # All final games in the current season
    python -m mlb.scripts.loaders.backfill_lineup_slots

    # Specific seasons
    python -m mlb.scripts.loaders.backfill_lineup_slots --season 2024 2025

    # A single game
    python -m mlb.scripts.loaders.backfill_lineup_slots --game-id 744914
"""

import argparse
import logging
import sys
from pathlib import Path
from typing import List, Optional, Tuple

import psycopg2
from psycopg2.extras import execute_values

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from mlb.config.database import get_players_db_config
from mlb.scripts.utilities.statsapi_wrapper import StatsAPIWrapper, get_current_season

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

DB_CONFIG = get_players_db_config()


def _parse_batting_order(raw) -> Optional[int]:
    """
    Convert a boxscore ``battingOrder`` value to a 1-9 lineup slot.

    Returns None for missing / unparseable / out-of-range values (those rows are
    skipped - never synthesized).
    """
    if raw is None:
        return None
    try:
        slot = int(str(raw)) // 100
    except (ValueError, TypeError):
        return None
    return slot if 1 <= slot <= 9 else None


def _slots_from_boxscore(box: dict, game_id: int, game_date: str) -> List[Tuple]:
    """
    Extract (player_id, game_id, game_date, batting_order) tuples from a boxscore.

    Iterates both home and away ``players`` maps; only players with a valid
    1-9 lineup slot are returned.
    """
    rows: List[Tuple] = []
    teams = (box or {}).get("teams", {})
    for side in ("home", "away"):
        players = teams.get(side, {}).get("players", {}) or {}
        for entry in players.values():
            slot = _parse_batting_order(entry.get("battingOrder"))
            if slot is None:
                continue
            person = entry.get("person", {}) or {}
            pid = person.get("id")
            if pid is None:
                continue
            rows.append((int(pid), game_id, game_date, slot))
    return rows


def _target_games(conn, seasons: List[int], game_id: Optional[int]) -> List[Tuple[int, str]]:
    """Resolve the (game_id, game_date) list to backfill from mlb_games.games."""
    cur = conn.cursor()
    if game_id is not None:
        cur.execute(
            "SELECT game_id, game_date FROM mlb_games.games WHERE game_id = %s",
            (game_id,),
        )
    else:
        # EXTRACT(YEAR ...) keeps this season-bounded without a games.season col.
        cur.execute(
            """
            SELECT game_id, game_date
              FROM mlb_games.games
             WHERE LOWER(status) = 'final'
               AND EXTRACT(YEAR FROM game_date)::int = ANY(%s)
             ORDER BY game_date
            """,
            (seasons,),
        )
    out: List[Tuple[int, str]] = []
    for gid, gdate in cur.fetchall():
        out.append(
            (int(gid), gdate.strftime("%Y-%m-%d") if hasattr(gdate, "strftime") else str(gdate))
        )
    cur.close()
    return out


def backfill_lineup_slots(seasons: List[int], game_id: Optional[int] = None) -> int:
    """
    Backfill batter lineup slots for the given seasons (or a single game).

    Args:
        seasons: List of 4-digit season years.
        game_id: Optional single game; otherwise all final games in the seasons.

    Returns:
        Total lineup-slot rows upserted.
    """
    api = StatsAPIWrapper()
    conn = psycopg2.connect(**DB_CONFIG)

    try:
        games = _target_games(conn, seasons, game_id)
        logger.info(f"Backfilling lineup slots for {len(games)} games, seasons {seasons}")

        total = 0
        for idx, (gid, gdate) in enumerate(games, 1):
            try:
                box = api.get_boxscore(gid)
            except (KeyError, TypeError, ValueError) as e:
                logger.warning(f"  boxscore fetch failed for game {gid}: {e}")
                continue

            rows = _slots_from_boxscore(box, gid, gdate)
            if not rows:
                continue

            cur = conn.cursor()
            try:
                execute_values(
                    cur,
                    """
                    INSERT INTO batter_lineup_slots
                        (player_id, game_id, game_date, batting_order)
                    VALUES %s
                    ON CONFLICT (player_id, game_id)
                    DO UPDATE SET batting_order = EXCLUDED.batting_order
                    """,
                    rows,
                )
                conn.commit()
                total += len(rows)
                if idx % 50 == 0 or idx == len(games):
                    logger.info(f"[{idx}/{len(games)}] {total} lineup-slot rows so far")
            except psycopg2.Error as e:
                conn.rollback()
                logger.error(f"  insert failed for game {gid}: {e}")
            finally:
                cur.close()

        logger.info(f"Backfilled {total} batter lineup-slot rows")
        return total

    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description="Backfill MLB batter lineup slots")
    parser.add_argument("--season", type=int, nargs="+", default=None, help="Season years")
    parser.add_argument("--game-id", type=int, default=None, help="Single game id")
    args = parser.parse_args()

    seasons = args.season or [get_current_season()]
    try:
        backfill_lineup_slots(seasons, game_id=args.game_id)
    except (psycopg2.Error, KeyError, TypeError, ValueError) as e:
        logger.error(f"Backfill failed: {e}")
        raise


if __name__ == "__main__":
    main()
