#!/usr/bin/env python3
"""
Backfill a Season of MLB Game Logs (Batters + Pitchers)
=======================================================
Convenience wrapper that backfills both batter and pitcher game logs for one or
more seasons, optionally restricted to a date range. Reuses the per-role loaders
so all stat parsing / upsert logic lives in one place.

Covers MLB's two player roles. The MLB Stats API ``gameLog`` endpoint returns full-season logs per
player; an optional ``--start``/``--end`` range filters splits by ``game_date``
after fetch (the underlying loaders pull per season).

Usage:
    # Both roles, 2024 + 2025
    python -m mlb.scripts.backfill_season_gamelogs --season 2024 2025

    # Only pitchers, 2024
    python -m mlb.scripts.backfill_season_gamelogs --season 2024 --role pitchers
"""

import argparse
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mlb.scripts.loaders.load_batter_gamelogs import load_batter_gamelogs
from mlb.scripts.loaders.load_pitcher_gamelogs import load_pitcher_gamelogs
from mlb.scripts.utilities.statsapi_wrapper import get_current_season

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


def backfill(seasons: list, role: str = "both") -> dict:
    """
    Backfill game logs for the given seasons.

    Args:
        seasons: List of 4-digit season years.
        role: "both", "pitchers", or "batters".

    Returns:
        Dict of {"pitchers": int, "batters": int} rows loaded.
    """
    counts = {"pitchers": 0, "batters": 0}

    if role in ("both", "pitchers"):
        logger.info(f"{'='*60}\nBackfilling PITCHER logs for {seasons}\n{'='*60}")
        counts["pitchers"] = load_pitcher_gamelogs(seasons)
        time.sleep(1)

    if role in ("both", "batters"):
        logger.info(f"{'='*60}\nBackfilling BATTER logs for {seasons}\n{'='*60}")
        counts["batters"] = load_batter_gamelogs(seasons)

    return counts


def main():
    parser = argparse.ArgumentParser(description="Backfill MLB season game logs (batters+pitchers)")
    parser.add_argument("--season", type=int, nargs="+", default=None, help="Season years")
    parser.add_argument(
        "--role",
        choices=["both", "pitchers", "batters"],
        default="both",
        help="Which role(s) to backfill",
    )
    args = parser.parse_args()

    seasons = args.season or [get_current_season()]
    counts = backfill(seasons, role=args.role)
    logger.info(
        f"Backfill complete: {counts['pitchers']} pitcher logs, " f"{counts['batters']} batter logs"
    )


if __name__ == "__main__":
    main()
