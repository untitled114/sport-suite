#!/usr/bin/env python3
"""
Incremental Game-Log Update (daily pipeline)
============================================
The full-season backfill (``backfill_season_gamelogs``) walks every profiled
player (~1,100) and takes ~50 minutes, too long for a daily job. This loader is the
incremental path:

1. Pull the schedule for a short date range (default: yesterday + today).
2. Read each game's boxscore ONCE to learn who actually appeared, split by
   role (pitchers vs batters who took a plate appearance).
3. Reuse the per-player loaders (``load_pitcher_gamelogs`` /
   ``load_batter_gamelogs``) for just those players - their upserts are
   idempotent and their parsing is the single source of truth.

Typical day: ~15 boxscore calls + ~300 player refreshes, about 5 minutes.

Usage:
    python -m mlb.scripts.loaders.update_recent_gamelogs                # yesterday..today
    python -m mlb.scripts.loaders.update_recent_gamelogs --start 2026-07-01 --end 2026-07-03
"""

import argparse
import logging
import sys
from datetime import datetime, timedelta
from typing import List, Optional, Set, Tuple
from zoneinfo import ZoneInfo

import requests

from mlb.scripts.loaders.load_batter_gamelogs import load_batter_gamelogs
from mlb.scripts.loaders.load_pitcher_gamelogs import load_pitcher_gamelogs

EST = ZoneInfo("America/New_York")

logger = logging.getLogger(__name__)

STATSAPI = "https://statsapi.mlb.com/api/v1"


def _get_json(url: str, timeout: int = 20) -> Optional[dict]:
    """GET a statsapi URL; None on any HTTP/parse failure (logged)."""
    try:
        resp = requests.get(url, timeout=timeout, headers={"Accept": "application/json"})
        resp.raise_for_status()
        return resp.json()
    except (requests.RequestException, ValueError) as e:
        logger.warning("statsapi request failed (%s): %s", url, e)
        return None


def game_pks(start: str, end: str) -> List[int]:
    """FINAL gamePks in [start, end] from the schedule endpoint.

    In-progress games are excluded: a live boxscore holds partial stat lines.
    On 2026-07-08, 39 of 312 results read while their game was live had
    actuals that changed afterwards. Live games are picked up by a later run
    once they finish.
    """
    data = _get_json(f"{STATSAPI}/schedule?sportId=1&startDate={start}&endDate={end}")
    if not data:
        return []
    pks = []
    for d in data.get("dates", []):
        for g in d.get("games", []):
            pk = g.get("gamePk")
            status = g.get("status")
            state = status.get("abstractGameState") if isinstance(status, dict) else None
            if pk and state == "Final":
                pks.append(int(pk))
    return pks


def players_in_game(game_pk: int) -> Tuple[Set[int], Set[int]]:
    """(pitcher_ids, batter_ids) who actually appeared in a game.

    A player counts as a pitcher when their boxscore pitching line shows
    batters faced, and as a batter when they took a plate appearance -
    role is derived from what they DID, not their roster position, so
    two-way players land in both sets.
    """
    data = _get_json(f"{STATSAPI}/game/{game_pk}/boxscore")
    pitchers: Set[int] = set()
    batters: Set[int] = set()
    if not data:
        return pitchers, batters
    for side in ("home", "away"):
        players = data.get("teams", {}).get(side, {}).get("players", {})
        for p in players.values():
            pid = p.get("person", {}).get("id")
            if not pid:
                continue
            stats = p.get("stats", {})
            if int(stats.get("pitching", {}).get("battersFaced", 0) or 0) > 0:
                pitchers.add(int(pid))
            if int(stats.get("batting", {}).get("plateAppearances", 0) or 0) > 0:
                batters.add(int(pid))
    return pitchers, batters


def update_range(start: str, end: str, season: Optional[int] = None) -> dict:
    """Refresh game logs for every player who appeared in [start, end]."""
    season = season or int(end[:4])
    pks = game_pks(start, end)
    logger.info("Found %d games in %s..%s", len(pks), start, end)

    pitchers: Set[int] = set()
    batters: Set[int] = set()
    for pk in pks:
        p, b = players_in_game(pk)
        pitchers |= p
        batters |= b
    logger.info("Players appeared: %d pitchers, %d batters", len(pitchers), len(batters))

    counts = {"games": len(pks), "pitchers": 0, "batters": 0, "errors": 0}
    for pid in sorted(pitchers):
        try:
            counts["pitchers"] += load_pitcher_gamelogs([season], player_id=pid)
        except Exception as e:
            counts["errors"] += 1
            logger.warning("pitcher %s refresh failed: %s", pid, e)
    for pid in sorted(batters):
        try:
            counts["batters"] += load_batter_gamelogs([season], player_id=pid)
        except Exception as e:
            counts["errors"] += 1
            logger.warning("batter %s refresh failed: %s", pid, e)
    return counts


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Incremental MLB game-log update")
    now = datetime.now(EST)
    parser.add_argument(
        "--start",
        default=(now - timedelta(days=1)).strftime("%Y-%m-%d"),
        help="Inclusive start date (default: yesterday EST)",
    )
    parser.add_argument(
        "--end",
        default=now.strftime("%Y-%m-%d"),
        help="Inclusive end date (default: today EST)",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    counts = update_range(args.start, args.end)
    logger.info("Incremental game-log update complete: %s", counts)
    return 0


if __name__ == "__main__":
    sys.exit(main())
