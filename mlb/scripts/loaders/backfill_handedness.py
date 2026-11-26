"""
MLB Handedness Backfill
=======================
Backfills the throwing/batting hand on the two player profile tables, which the
roster loader (``load_mlb_players``) does not populate:

    pitcher_profile.throws  <- /people/{id}.pitchHand.code  (L / R / S)
    batter_profile.bats     <- /people/{id}.batSide.code     (L / R / S)

Handedness is the join key for the matchup feature family (opposing-pitcher hand
and the batter platoon split), so it must be filled for those features to be
non-constant. ``player_id`` is the MLBAM id and equals ``/people/{id}``.

The MLB Stats API ``/people`` endpoint accepts a comma-separated list of ids, so
profiles are fetched in batches to keep request counts low.

Usage:
    python -m mlb.scripts.loaders.backfill_handedness
    python -m mlb.scripts.loaders.backfill_handedness --batch-size 50
"""

import argparse
import logging
import sys
from pathlib import Path
from typing import Dict, List

import psycopg2
from psycopg2.extras import execute_values

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from mlb.config.database import get_players_db_config
from mlb.scripts.utilities.statsapi_wrapper import StatsAPIWrapper

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# (profile table, hand column, /people field, sub-field)
_ROLES = (
    ("pitcher_profile", "throws", "pitchHand"),
    ("batter_profile", "bats", "batSide"),
)


class HandednessBackfiller:
    """Fills throws/bats on pitcher_profile/batter_profile from the Stats API."""

    def __init__(self, batch_size: int = 100, api: StatsAPIWrapper = None):
        self.batch_size = max(1, batch_size)
        self.api = api or StatsAPIWrapper()
        self.conn = None

    def connect(self) -> None:
        self.conn = psycopg2.connect(**get_players_db_config())
        logger.info("Connected to mlb_players database")

    def close(self) -> None:
        if self.conn:
            self.conn.close()
            logger.info("Database connection closed")

    def _fetch_ids(self, table: str) -> List[int]:
        """Return all profile player_ids for a role (all are (re)filled)."""
        with self.conn.cursor() as cur:
            cur.execute(f"SELECT player_id FROM {table} ORDER BY player_id")
            return [r[0] for r in cur.fetchall()]

    def _fetch_hands(self, ids: List[int], field: str) -> Dict[int, str]:
        """Fetch ``{player_id: hand_code}`` for a batch of ids via /people."""
        hands: Dict[int, str] = {}
        for start in range(0, len(ids), self.batch_size):
            chunk = ids[start : start + self.batch_size]
            data = self.api.get_people(chunk)
            for person in data:
                pid = person.get("id")
                code = (person.get(field) or {}).get("code")
                if pid is not None and code:
                    hands[pid] = str(code).strip().upper()
        return hands

    def backfill_role(self, table: str, col: str, field: str) -> int:
        """Backfill one role's hand column; returns the number of rows updated."""
        ids = self._fetch_ids(table)
        if not ids:
            logger.info("%s: no profiles to backfill", table)
            return 0
        hands = self._fetch_hands(ids, field)
        rows = [(pid, hand) for pid, hand in hands.items()]
        if not rows:
            logger.warning("%s: API returned no handedness for %d ids", table, len(ids))
            return 0
        with self.conn.cursor() as cur:
            execute_values(
                cur,
                f"""
                UPDATE {table} AS p
                SET {col} = v.hand, updated_at = NOW()
                FROM (VALUES %s) AS v(player_id, hand)
                WHERE p.player_id = v.player_id
                """,
                rows,
            )
            updated = cur.rowcount
        self.conn.commit()
        logger.info("%s.%s: updated %d / %d profiles", table, col, updated, len(ids))
        return updated

    def run(self) -> Dict[str, int]:
        """Backfill both roles; returns ``{table: rows_updated}``."""
        results: Dict[str, int] = {}
        for table, col, field in _ROLES:
            results[table] = self.backfill_role(table, col, field)
        return results


def main():
    parser = argparse.ArgumentParser(description="Backfill MLB pitcher/batter handedness")
    parser.add_argument("--batch-size", type=int, default=100, help="ids per /people request")
    args = parser.parse_args()

    backfiller = HandednessBackfiller(batch_size=args.batch_size)
    try:
        backfiller.connect()
        results = backfiller.run()
        total = sum(results.values())
        logger.info("Handedness backfill complete: %s (total %d)", results, total)
    finally:
        backfiller.close()


if __name__ == "__main__":
    main()
