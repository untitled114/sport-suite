"""
games.games.start_time from ESPN's scoreboard.

The start time is ESPN's event.date. Stored as timestamptz; shown in ET.

A game is matched by its date (the scoreboard date queried, in ET, as fetch_daily_stats files
games; a late tip's event.date is the next day in UTC) and its two teams in either orientation,
so neutral-site games (the NBA Cup final, the London and Berlin games) match however each side
lists home. Only rows that exist are touched, and only their NULL start_time: this path never
inserts a game.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Iterable, List, Tuple
from zoneinfo import ZoneInfo

from nba.utils.team_utils import normalize_team_abbrev

EST = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class GameStart:
    away: str
    home: str
    start_time: datetime  # aware
    espn_id: str
    neutral_site: bool


def scoreboard_starts(events: Iterable[dict]) -> List[GameStart]:
    """The start of each game in an ESPN scoreboard's events (a game without a time is left out)."""
    out = []
    for event in events:
        when = event.get("date")
        comps = event.get("competitions") or []
        if not when or not comps:
            continue
        sides = {
            c.get("homeAway"): c.get("team", {}).get("abbreviation")
            for c in comps[0].get("competitors", [])
        }
        if not sides.get("home") or not sides.get("away"):
            continue
        out.append(
            GameStart(
                away=normalize_team_abbrev(sides["away"]),
                home=normalize_team_abbrev(sides["home"]),
                start_time=datetime.fromisoformat(when.replace("Z", "+00:00")).astimezone(EST),
                espn_id=str(event.get("id", "")),
                neutral_site=bool(comps[0].get("neutralSite")),
            )
        )
    return out


def fill_start_times(
    conn: Any, game_date: date, starts: Iterable[GameStart]
) -> Tuple[int, List[GameStart]]:
    """Fill the NULL start_time of game_date's rows from ``starts``; never insert.

    Returns (rows filled, the starts that matched no row). Does not commit.
    """
    filled, unmatched = 0, []
    with conn.cursor() as cur:
        for s in starts:
            pair = ((s.home, s.away), (s.away, s.home))
            cur.execute(
                "SELECT count(*) FROM games.games WHERE game_date = %s "
                "AND (home_team, away_team) IN (%s, %s)",
                (game_date, *pair),
            )
            if cur.fetchone()[0] == 0:
                unmatched.append(s)
                continue
            cur.execute(
                "UPDATE games.games SET start_time = %s WHERE game_date = %s "
                "AND (home_team, away_team) IN (%s, %s) AND start_time IS NULL",
                (s.start_time, game_date, *pair),
            )
            filled += cur.rowcount
    return filled, unmatched


SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard?dates={}"


def read_scoreboard(game_date: date) -> List[GameStart]:  # pragma: no cover
    """ESPN's scoreboard for one date, as game starts."""
    import json
    import urllib.request

    url = SCOREBOARD.format(f"{game_date:%Y%m%d}")
    with urllib.request.urlopen(url, timeout=15) as resp:  # nosec B310
        return scoreboard_starts(json.loads(resp.read()).get("events", []))


def fill_for_date(game_date: date) -> Tuple[int, List[GameStart]]:  # pragma: no cover
    """Fill the day's missing start times from the scoreboard and commit."""
    from nba.config.database import get_connection

    conn = get_connection("games")
    try:
        filled, unmatched = fill_start_times(conn, game_date, read_scoreboard(game_date))
        conn.commit()
        return filled, unmatched
    finally:
        conn.close()


def main() -> int:  # pragma: no cover
    """Fill one day's start times: the pipeline runs this right after the day's games rows are
    written, so every later step of the day knows when each game starts."""
    import argparse
    import logging

    parser = argparse.ArgumentParser(description="Fill games.games.start_time for one date")
    parser.add_argument("--date", required=True, help="YYYY-MM-DD (Eastern)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    filled, unmatched = fill_for_date(date.fromisoformat(args.date))
    print(
        f"start_time filled for {filled} game(s) on {args.date}; {len(unmatched)} not in the table"
    )
    for s in unmatched:
        print(f"  no games row for {s.away} @ {s.home} (ESPN {s.espn_id})")
    return 0


if __name__ == "__main__":  # pragma: no cover
    import sys

    sys.exit(main())
