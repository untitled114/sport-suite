#!/usr/bin/env python3
"""
Fetch Today's MLB Slate (Live Path)
===================================
Lightweight loader for the daily pipeline: fetches today's games + probable
pitchers from the MLB Stats API ``/schedule`` endpoint and upserts them into
``mlb_games.games`` and ``mlb_games.probable_pitchers``.

Mirrors ``nba/scripts/loaders/fetch_nba_current_games.py`` in role, but is a
thin wrapper over the shared ``load_mlb_games_incremental`` logic so the
schedule-parsing code lives in exactly one place.

Usage:
    # Fetch today's slate (preview only)
    python -m mlb.scripts.loaders.fetch_mlb_current_games

    # Fetch a specific date and write to DB
    python -m mlb.scripts.loaders.fetch_mlb_current_games --date 2024-07-01 --insert
"""

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from mlb.scripts.loaders.load_mlb_games_incremental import _parse_game, load_games
from mlb.scripts.utilities.statsapi_wrapper import StatsAPIWrapper

EST = ZoneInfo("America/New_York")

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


def preview_slate(date: str) -> list:
    """
    Fetch today's slate without writing to DB. Returns a list of dicts
    describing each game (for logging / live-path callers).
    """
    api = StatsAPIWrapper()
    schedule = api.get_schedule(date)
    slate = []
    for game in schedule:
        grow, prows = _parse_game(game)
        if grow is None:
            continue
        slate.append(
            {
                "game_id": grow[0],
                "game_date": grow[1],
                "home_team": grow[2],
                "away_team": grow[3],
                "status": grow[6],
                "venue": grow[7],
                "probables": [{"team": p[2], "pitcher": p[4]} for p in prows],
            }
        )
    logger.info(f"{date}: {len(slate)} games on the slate")
    for g in slate:
        probs = ", ".join(f"{p['team']}:{p['pitcher']}" for p in g["probables"])
        logger.info(f"  {g['away_team']} @ {g['home_team']} ({g['status']}) [{probs}]")
    return slate


def main():
    parser = argparse.ArgumentParser(description="Fetch today's MLB slate + probables")
    today = datetime.now(EST).strftime("%Y-%m-%d")
    parser.add_argument("--date", type=str, default=today, help="Date YYYY-MM-DD (EST)")
    parser.add_argument("--insert", action="store_true", help="Write games + probables to DB")
    args = parser.parse_args()

    if args.insert:
        n = load_games(args.date, args.date)
        logger.info(f"Upserted {n} games for {args.date}")
    else:
        preview_slate(args.date)
        logger.info("Preview complete (use --insert to write to DB)")


if __name__ == "__main__":
    main()
