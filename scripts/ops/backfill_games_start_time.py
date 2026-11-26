#!/usr/bin/env python3
"""Fill games.games.start_time from ESPN's scoreboard, one request per date.

For each date in [--start, --end] that has a game with no start_time, it fetches ESPN's
scoreboard for that date and fills the NULL start_time of the rows it matches by date and teams
(nba/scripts/game_start_times.py). It never inserts a game and never overwrites a time. Without
--apply it reports what it would fill and changes nothing.

It writes only to the database whose system id is --system-id, which must be given, so a run
cannot land on a database it was not aimed at.

    python scripts/ops/backfill_games_start_time.py --start 2023-10-24 [--end YYYY-MM-DD] [--apply]
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from nba.scripts.game_start_times import fill_start_times, scoreboard_starts  # noqa: E402

EST = ZoneInfo("America/New_York")
ESPN_SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard"


def dates_missing(conn, start: date, end: date):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT game_date FROM games.games "
            "WHERE start_time IS NULL AND game_date BETWEEN %s AND %s ORDER BY game_date",
            (start, end),
        )
        return [r[0] for r in cur.fetchall()]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--start", default="2023-10-24")
    ap.add_argument("--end", default=datetime.now(EST).date().isoformat())
    ap.add_argument(
        "--system-id",
        type=int,
        required=True,
        help="the system identifier of the database to write to (SELECT system_identifier FROM pg_control_system()); the run refuses any other database",
    )
    ap.add_argument("--apply", action="store_true", help="write (default: dry run)")
    ap.add_argument("--pause", type=float, default=0.5, help="seconds between ESPN requests")
    args = ap.parse_args(argv)

    from nba.config.database import get_connection

    conn = get_connection("games", autocommit=False)
    with conn.cursor() as cur:
        cur.execute("SELECT system_identifier FROM pg_control_system()")
        found = int(cur.fetchone()[0])
    if found != args.system_id:
        print(f"refusing: this database's system id is {found}, not {args.system_id}")
        return 2

    days = dates_missing(conn, date.fromisoformat(args.start), date.fromisoformat(args.end))
    print(f"{len(days)} dates with a game missing its start_time")
    filled_total, unmatched_total, failed = 0, 0, []
    session = requests.Session()
    for d in days:
        try:
            r = session.get(
                ESPN_SCOREBOARD_URL, params={"dates": d.strftime("%Y%m%d"), "limit": 50}, timeout=20
            )
            r.raise_for_status()
            events = r.json().get("events", [])
        except (requests.RequestException, ValueError) as e:
            failed.append((d, type(e).__name__))
            continue
        filled, unmatched = fill_start_times(conn, d, scoreboard_starts(events))
        filled_total += filled
        unmatched_total += len(unmatched)
        for s in unmatched:
            print(f"  {d}: no games row for {s.away} @ {s.home} (ESPN {s.espn_id})")
        if args.apply:
            conn.commit()
        else:
            conn.rollback()
        time.sleep(args.pause)
    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM games.games WHERE start_time IS NULL AND game_date BETWEEN %s AND %s",
            (args.start, args.end),
        )
        still_null = cur.fetchone()[0]
    conn.close()
    mode = "filled" if args.apply else "would fill"
    print(
        f"{mode} {filled_total} rows; ESPN games with no row: {unmatched_total}; "
        f"dates that failed: {len(failed)} {failed[:10]}; rows still NULL: {still_null}"
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
