#!/usr/bin/env python3
"""Add the player box-score rows players.player_game_logs is missing, from ESPN.

Written for two gaps: 2025-26 March .. June (the playoffs had no player rows) and 2022-23
(35% of team-games complete). A team-game is short when its player rows' points don't add up to
games.team_game_logs' points (which match ESPN's final scores). For each date with a short
team-game, this reads ESPN's scoreboard and the summaries of the games with a short team, as
fetch_daily_stats does (same parse, parse_player_stats; same names, PlayerLookup), and inserts
the box-score row of each player who has none on that date. It never updates a row: an existing row
whose stats differ from ESPN's is reported, not changed.

game_id follows the season's convention: the id the team's other rows that date carry, else the
games.games id for seasons through 2024-25, else fetch_daily_stats' "<date>_<team>".

It writes only to the database whose system id is --system-id, which must be given, so a run
cannot land on a database it was not aimed at. Without
--apply it reports and changes nothing. --report writes the per-date counts, the differing
existing rows and the unmapped names as JSON.

    python scripts/ops/backfill_player_box_scores.py --start 2022-10-18 --end 2023-04-09 [--apply]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from nba.scripts.fetch_daily_stats import (  # noqa: E402
    ESPN_BOXSCORE_URL,
    ESPN_SCOREBOARD_URL,
    parse_player_stats,
)
from nba.sql import named_query
from nba.utils.player_lookup import PlayerLookup  # noqa: E402
from nba.utils.season import season_for_date  # noqa: E402
from nba.utils.team_utils import normalize_team_abbrev  # noqa: E402

STATS = (
    "minutes_played",
    "points",
    "rebounds",
    "assists",
    "steals",
    "blocks",
    "turnovers",
    "three_pointers_made",
    "three_pt_attempted",
    "fg_made",
    "fg_attempted",
    "ft_made",
    "ft_attempted",
    "plus_minus",
)
COLUMNS = (
    "player_id",
    "game_id",
    "game_date",
    "season",
    "team_abbrev",
    "opponent_abbrev",
    "is_home",
) + STATS

SHORT_TEAM_GAMES = named_query("box_score_team_points_mismatch")


def short_team_games(conn: Any, start: date, end: date) -> Dict[date, set]:
    """Team-games whose player rows don't add up to the team's points, by date."""
    out: Dict[date, set] = {}
    with conn.cursor() as cur:
        cur.execute(SHORT_TEAM_GAMES, {"start": start, "end": end})
        for d, team in cur.fetchall():
            out.setdefault(d, set()).add(team)
    return out


def final_games(events: List[dict]) -> List[dict]:
    """(espn id, home, away) of each finished game in a scoreboard's events."""
    out = []
    for event in events:
        comp = (event.get("competitions") or [{}])[0]
        if comp.get("status", {}).get("type", {}).get("name") != "STATUS_FINAL":
            continue
        sides = {
            c.get("homeAway"): c.get("team", {}).get("abbreviation")
            for c in comp.get("competitors", [])
        }
        if sides.get("home") and sides.get("away"):
            out.append(
                {
                    "espn_id": str(event.get("id")),
                    "home": normalize_team_abbrev(sides["home"]),
                    "away": normalize_team_abbrev(sides["away"]),
                }
            )
    return out


def summary_lines(summary: dict, home: str, away: str) -> List[dict]:
    """Each played line in an ESPN summary: name, team, opponent, is_home and the stats."""
    out = []
    for team_data in summary.get("boxscore", {}).get("players", []):
        team = normalize_team_abbrev(team_data.get("team", {}).get("abbreviation", ""))
        if team not in (home, away):
            continue
        for group in team_data.get("statistics", []):
            labels = group.get("labels", [])
            for athlete in group.get("athletes", []):
                line = parse_player_stats(athlete, labels)
                if line is None:
                    continue
                out.append(
                    {
                        "name": athlete.get("athlete", {}).get("displayName", ""),
                        "team": team,
                        "opponent": away if team == home else home,
                        "is_home": team == home,
                        **line,
                    }
                )
    return out


def _game_id(cur: Any, d: date, team: str, home: str, away: str) -> Optional[str]:
    """The game_id a new row takes, by the season's convention (module docstring)."""
    cur.execute(
        "SELECT game_id, count(*) FROM players.player_game_logs WHERE game_date = %s "
        "AND team_abbrev = %s GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT 1",
        (d, team),
    )
    row = cur.fetchone()
    if row:
        return row[0]
    if season_for_date(d) > 2025:
        return f"{d.isoformat()}_{team}"
    cur.execute(
        "SELECT game_id FROM games.games WHERE game_date = %s "
        "AND (home_team, away_team) IN ((%s, %s), (%s, %s)) ORDER BY game_id",
        (d, home, away, away, home),
    )
    ids = [r[0] for r in cur.fetchall()]
    return ids[0] if len(ids) == 1 else None


def backfill_date(
    conn: Any,
    lookup: PlayerLookup,
    d: date,
    short_teams: set,
    events: List[dict],
    fetch_summary: Callable[[str], Optional[dict]],
) -> Dict[str, Any]:
    """Insert the missing rows of ``d``'s short team-games. Does not commit."""
    report: Dict[str, Any] = {
        "date": d.isoformat(),
        "inserted": 0,
        "existing_same": 0,
        "existing_differ": [],
        "unmapped": [],
        "summary_failed": [],
        "no_game_id": [],
    }
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT player_id, {', '.join(STATS)} FROM players.player_game_logs WHERE game_date = %s",
            (d,),
        )
        existing = {r[0]: dict(zip(STATS, r[1:])) for r in cur.fetchall()}
        for game in final_games(events):
            if not {game["home"], game["away"]} & short_teams:
                continue
            summary = fetch_summary(game["espn_id"])
            if summary is None:
                report["summary_failed"].append(game["espn_id"])
                continue
            for line in summary_lines(summary, game["home"], game["away"]):
                player_id = lookup.resolve(line["name"], d)
                if player_id is None:
                    report["unmapped"].append(f"{line['team']} {line['name']}")
                    continue
                if player_id in existing:
                    stored = existing[player_id]
                    differ = {k: (stored[k], line[k]) for k in STATS if stored[k] != line[k]}
                    if differ:
                        report["existing_differ"].append({"player_id": player_id, **differ})
                    else:
                        report["existing_same"] += 1
                    continue
                game_id = _game_id(cur, d, line["team"], game["home"], game["away"])
                if game_id is None:
                    report["no_game_id"].append(f"{line['team']} {game['espn_id']}")
                    continue
                row = {
                    "player_id": player_id,
                    "game_id": game_id,
                    "game_date": d,
                    "season": season_for_date(d),
                    "team_abbrev": line["team"],
                    "opponent_abbrev": line["opponent"],
                    "is_home": line["is_home"],
                    **{k: line[k] for k in STATS},
                }
                cur.execute(
                    f"INSERT INTO players.player_game_logs ({', '.join(COLUMNS)}) "
                    f"VALUES ({', '.join(['%s'] * len(COLUMNS))})",
                    tuple(row[c] for c in COLUMNS),
                )
                existing[player_id] = {k: line[k] for k in STATS}
                report["inserted"] += 1
    return report


def main(argv=None) -> int:
    import requests

    from nba.config.database import get_connection

    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument(
        "--system-id",
        type=int,
        required=True,
        help="the system identifier of the database to write to (SELECT system_identifier FROM pg_control_system()); the run refuses any other database",
    )
    ap.add_argument("--apply", action="store_true", help="write (default: dry run)")
    ap.add_argument("--pause", type=float, default=0.5, help="seconds between ESPN requests")
    ap.add_argument("--report", help="write the JSON report here")
    args = ap.parse_args(argv)
    start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)

    conn = get_connection("players", autocommit=False)
    with conn.cursor() as cur:
        cur.execute("SELECT system_identifier FROM pg_control_system()")
        found = int(cur.fetchone()[0])
    if found != args.system_id:
        print(f"refusing: this database's system id is {found}, not {args.system_id}")
        return 2

    session = requests.Session()
    session.headers.update({"User-Agent": "sport-suite/3.0"})

    def get(url: str, params: dict) -> Optional[dict]:
        try:
            r = session.get(url, params=params, timeout=20)
            r.raise_for_status()
            return r.json()
        except (requests.RequestException, ValueError):
            return None
        finally:
            time.sleep(args.pause)

    lookup = PlayerLookup(conn)
    short = short_team_games(conn, start, end)
    print(f"{len(short)} dates with a short team-game ({sum(map(len, short.values()))} team-games)")
    reports, failed_dates = [], []
    for d in sorted(short):
        board = get(ESPN_SCOREBOARD_URL, {"dates": d.strftime("%Y%m%d"), "limit": 50})
        if board is None:
            failed_dates.append(d.isoformat())
            continue
        rep = backfill_date(
            conn,
            lookup,
            d,
            short[d],
            board.get("events", []),
            lambda espn_id: get(ESPN_BOXSCORE_URL, {"event": espn_id}),
        )
        reports.append(rep)
        if args.apply:
            conn.commit()
        else:
            conn.rollback()
        print(
            f"{d} inserted {rep['inserted']} same {rep['existing_same']} "
            f"differ {len(rep['existing_differ'])} unmapped {len(rep['unmapped'])} "
            f"failed {len(rep['summary_failed'])} no_id {len(rep['no_game_id'])}",
            flush=True,
        )
    still_short = sum(map(len, short_team_games(conn, start, end).values()))
    conn.close()
    unmapped = Counter(u for r in reports for u in r["unmapped"])
    mode = "inserted" if args.apply else "would insert"
    print(
        f"{mode} {sum(r['inserted'] for r in reports)} rows; existing rows differing from ESPN "
        f"(unchanged): {sum(len(r['existing_differ']) for r in reports)}; unmapped lines: "
        f"{sum(unmapped.values())} ({len(unmapped)} names); summaries failed: "
        f"{sum(len(r['summary_failed']) for r in reports)}; dates failed: {len(failed_dates)}; "
        f"team-games still short: {still_short}"
    )
    if args.report:
        Path(args.report).write_text(
            json.dumps(
                {
                    "start": args.start,
                    "end": args.end,
                    "apply": args.apply,
                    "dates": reports,
                    "failed_dates": failed_dates,
                    "unmapped": unmapped.most_common(),
                },
                indent=2,
                default=str,
            )
        )
    return 1 if failed_dates else 0


if __name__ == "__main__":
    sys.exit(main())
