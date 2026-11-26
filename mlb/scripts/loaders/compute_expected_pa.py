#!/usr/bin/env python3
"""
Expected-PA-by-Lineup-Slot Computation Loader
=============================================
Computes the lineup-slot -> expected plate-appearances/game curve from the stored
games and persists it to ``mlb_teams.expected_pa_by_slot`` (created by
``mlb/db/schema_expected_pa.sql``).

For each starting lineup slot 1..9:

    mean_pa(slot) = AVG(batter_game_logs.plate_appearances)
                    over every game a batter STARTED in that slot,

via ``mlb_players.batter_lineup_slots`` (the boxscore-sourced batting order)
JOINed to ``mlb_players.batter_game_logs`` on ``player_id + game_id``. Both are
filtered to ``[start, end]`` and ``plate_appearances IS NOT NULL`` so only real
observed games count. ``n_games`` is persisted for sample-size gating.

Usage:
    python -m mlb.scripts.loaders.compute_expected_pa
    python -m mlb.scripts.loaders.compute_expected_pa --start 2024 --end 2026
"""

import argparse
import logging
import sys
from pathlib import Path
from typing import Dict

import psycopg2
from psycopg2.extras import execute_values

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from mlb.config.database import get_team_db_config
from mlb.scripts.utilities.statsapi_wrapper import get_current_season
from mlb.sql import named_query

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

DB_CONFIG = get_team_db_config()


def _fetch_slot_means(conn, start: int, end: int) -> Dict[int, Dict[str, float]]:
    """
    Return ``{slot: {"mean_pa", "n_games"}}`` for slots 1..9 from real data.
    """
    cur = conn.cursor()
    cur.execute(
        named_query("plate_appearances_by_lineup_slot"),
        (start, end),
    )
    out: Dict[int, Dict[str, float]] = {}
    for slot, mean_pa, n in cur.fetchall():
        out[int(slot)] = {"mean_pa": round(float(mean_pa), 4), "n_games": int(n)}
    cur.close()
    return out


def _persist(conn, window_label: str, curve: Dict[int, Dict[str, float]]) -> int:
    """UPSERT the computed curve into mlb_teams.expected_pa_by_slot."""
    rows = [
        (slot, window_label, vals["mean_pa"], vals["n_games"])
        for slot, vals in sorted(curve.items())
    ]
    if not rows:
        return 0
    cur = conn.cursor()
    execute_values(
        cur,
        """
        INSERT INTO mlb_teams.expected_pa_by_slot
            (lineup_slot, window_label, mean_pa, n_games)
        VALUES %s
        ON CONFLICT (lineup_slot, window_label) DO UPDATE SET
            mean_pa     = EXCLUDED.mean_pa,
            n_games     = EXCLUDED.n_games,
            computed_at = now()
        """,
        rows,
    )
    conn.commit()
    cur.close()
    return len(rows)


def compute_expected_pa(start: int, end: int) -> Dict[int, Dict[str, float]]:
    """
    Compute and persist the slot -> expected-PA curve for ``[start, end]``.

    Returns the ``{slot: {mean_pa, n_games}}`` mapping that was written.
    """
    window_label = f"rolling_{start}_{end}"
    conn = psycopg2.connect(**DB_CONFIG)
    try:
        curve = _fetch_slot_means(conn, start, end)
        written = _persist(conn, window_label, curve)
        logger.info("Computed expected-PA curve for %d slots (window %s)", written, window_label)
        for slot in sorted(curve):
            v = curve[slot]
            logger.info("  slot %d: mean_pa=%s (n=%d)", slot, v["mean_pa"], v["n_games"])
        return curve
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description="Compute MLB expected PA by lineup slot")
    parser.add_argument("--start", type=int, default=None, help="First season (default current-2)")
    parser.add_argument("--end", type=int, default=None, help="Last season (default current)")
    args = parser.parse_args()

    end = args.end or get_current_season()
    start = args.start or (end - 2)
    try:
        compute_expected_pa(start, end)
    except (psycopg2.Error, KeyError, TypeError, ValueError) as e:
        logger.error(f"Expected-PA computation failed: {e}")
        raise


if __name__ == "__main__":
    main()
