"""
Unit tests for the MLB data ingestion loaders.

The StatsAPIWrapper (HTTP) and psycopg2 (DB) are fully mocked. No network or DB
access occurs. The payloads are the smallest shapes that exercise the parsing and the SQL
parameters.
"""

from unittest.mock import MagicMock, patch

import pytest

import mlb.scripts.backfill_season_gamelogs as backfill
import mlb.scripts.loaders.calculate_team_stats as team_stats
import mlb.scripts.loaders.fetch_mlb_current_games as current_games
import mlb.scripts.loaders.load_batter_gamelogs as batter_logs
import mlb.scripts.loaders.load_mlb_games_incremental as games_inc
import mlb.scripts.loaders.load_mlb_players as players
import mlb.scripts.loaders.load_mlb_teams as teams
import mlb.scripts.loaders.load_pitcher_gamelogs as pitcher_logs


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def make_conn(fetch_results=None):
    """Build a mock psycopg2 connection whose cursor returns fetch_results."""
    conn = MagicMock()
    cursor = MagicMock()
    cursor.fetchall.return_value = fetch_results or []
    cursor.fetchone.return_value = fetch_results[0] if fetch_results else None
    conn.cursor.return_value = cursor
    return conn, cursor


# ---------------------------------------------------------------------------
# load_mlb_teams
# ---------------------------------------------------------------------------
def test_load_teams_inserts_rows():
    loader = teams.MLBTeamLoader()
    loader.conn, cursor = make_conn()
    loader.api = MagicMock()
    loader.api.get_teams.return_value = [
        {
            "id": 147,
            "abbreviation": "NYY",
            "name": "New York Yankees",
            "league": {"name": "American League"},
            "division": {"name": "AL East"},
            "venue": {"name": "Yankee Stadium"},
        }
    ]
    with patch.object(teams, "execute_values") as ev:
        n = loader.load_teams()
    assert n == 1
    rows = ev.call_args[0][2]
    assert rows[0][0] == 147 and rows[0][1] == "NYY"
    assert rows[0][3] == "American League"


def test_load_teams_empty():
    loader = teams.MLBTeamLoader()
    loader.conn, _ = make_conn()
    loader.api = MagicMock()
    loader.api.get_teams.return_value = []
    assert loader.load_teams() == 0


# ---------------------------------------------------------------------------
# load_mlb_players
# ---------------------------------------------------------------------------
def test_load_rosters_splits_pitchers_and_batters():
    loader = players.MLBPlayerLoader()
    loader.conn, _ = make_conn()
    loader.api = MagicMock()
    loader.api.get_team_id_map.return_value = {147: "NYY"}
    loader.api.get_roster.return_value = [
        {
            "person": {"id": 1, "fullName": "Pitcher One"},
            "position": {"abbreviation": "P", "type": "Pitcher"},
        },
        {
            "person": {"id": 2, "fullName": "Batter Two"},
            "position": {"abbreviation": "SS", "type": "Infielder"},
        },
        {
            "person": {"id": 3, "fullName": "Two Way"},
            "position": {"abbreviation": "DH", "type": "Two-Way Player"},
        },
    ]
    with patch.object(players, "execute_values") as ev:
        counts = loader.load_rosters(team_id=147)
    # two-way counts as both
    assert counts == {"pitchers": 2, "batters": 2}
    # two execute_values calls (pitcher then batter)
    assert ev.call_count == 2


def test_load_rosters_skips_missing_id():
    loader = players.MLBPlayerLoader()
    loader.conn, _ = make_conn()
    loader.api = MagicMock()
    loader.api.get_team_id_map.return_value = {147: "NYY"}
    loader.api.get_roster.return_value = [
        {"person": {"id": None, "fullName": "Ghost"}, "position": {"type": "Pitcher"}},
        {"person": {"id": 5, "fullName": ""}, "position": {"type": "Pitcher"}},
    ]
    with patch.object(players, "execute_values"):
        counts = loader.load_rosters(team_id=147)
    assert counts == {"pitchers": 0, "batters": 0}


# ---------------------------------------------------------------------------
# load_mlb_games_incremental
# ---------------------------------------------------------------------------
def test_date_range_inclusive():
    assert games_inc._date_range("2024-07-01", "2024-07-03") == [
        "2024-07-01",
        "2024-07-02",
        "2024-07-03",
    ]


def test_parse_game_final_with_probables():
    game = {
        "gamePk": 744914,
        "officialDate": "2024-07-01",
        "status": {"detailedState": "Final"},
        "venue": {"name": "Rogers Centre"},
        "teams": {
            "home": {
                "score": 5,
                "team": {"abbreviation": "TOR"},
                "probablePitcher": {"id": 684320, "fullName": "Yariel Rodríguez"},
            },
            "away": {
                "score": 3,
                "team": {"abbreviation": "BOS"},
                "probablePitcher": {"id": 999, "fullName": "Some One"},
            },
        },
    }
    grow, prows = games_inc._parse_game(game)
    assert grow[0] == 744914
    assert grow[2] == "TOR" and grow[3] == "BOS"
    assert grow[4] == 5 and grow[5] == 3  # scores kept (Final)
    assert grow[6] == "Final"
    assert len(prows) == 2
    assert prows[0] == (744914, "2024-07-01", "TOR", 684320, "Yariel Rodríguez")


def test_parse_game_scheduled_drops_scores():
    game = {
        "gamePk": 1,
        "officialDate": "2024-07-01",
        "status": {"detailedState": "Scheduled"},
        "teams": {
            "home": {"score": None, "team": {"abbreviation": "TOR"}},
            "away": {"score": None, "team": {"abbreviation": "BOS"}},
        },
    }
    grow, prows = games_inc._parse_game(game)
    assert grow[4] is None and grow[5] is None
    assert prows == []


def test_parse_game_no_gamepk():
    grow, prows = games_inc._parse_game({})
    assert grow is None and prows == []


def test_parse_game_gamedate_fallback_to_est():
    game = {
        "gamePk": 2,
        "gameDate": "2024-07-02T00:30:00Z",  # 00:30 UTC -> previous EST day
        "status": {"detailedState": "Scheduled"},
        "teams": {
            "home": {"team": {"abbreviation": "LAD"}},
            "away": {"team": {"abbreviation": "SF"}},
        },
    }
    grow, _ = games_inc._parse_game(game)
    assert grow[1] == "2024-07-01"  # converted to EST


def test_load_games_upserts(monkeypatch):
    api = MagicMock()
    api.get_schedule.return_value = [
        {
            "gamePk": 10,
            "officialDate": "2024-07-01",
            "status": {"detailedState": "Final"},
            "venue": {"name": "V"},
            "teams": {
                "home": {"score": 1, "team": {"abbreviation": "A"}},
                "away": {"score": 0, "team": {"abbreviation": "B"}},
            },
        }
    ]
    monkeypatch.setattr(games_inc, "StatsAPIWrapper", lambda *a, **k: api)
    conn, _ = make_conn()
    monkeypatch.setattr(games_inc.psycopg2, "connect", lambda **k: conn)
    with patch.object(games_inc, "execute_values") as ev:
        n = games_inc.load_games("2024-07-01", "2024-07-01")
    assert n == 1
    # games insert called (at least once)
    assert ev.call_count >= 1


# ---------------------------------------------------------------------------
# load_pitcher_gamelogs
# ---------------------------------------------------------------------------
def test_innings_to_float():
    assert pitcher_logs._innings_to_float("5.2") == pytest.approx(5 + 2 / 3)
    assert pitcher_logs._innings_to_float("6.0") == 6.0
    assert pitcher_logs._innings_to_float(None) is None
    assert pitcher_logs._innings_to_float("bad") is None


def test_pitcher_parse_split_resolves_team_via_map():
    split = {
        "date": "2024-04-13",
        "isHome": True,
        "game": {"gamePk": 744953},
        "team": {"id": 141},  # no abbreviation -> resolved via map
        "opponent": {"id": 115},
        "stat": {
            "inningsPitched": "3.2",
            "strikeOuts": 6,
            "battersFaced": 15,
            "numberOfPitches": 70,
            "earnedRuns": 2,
            "hits": 4,
            "baseOnBalls": 1,
        },
    }
    row = pitcher_logs._parse_split(split, 684320, "P One", {141: "TOR", 115: "COL"})
    assert row[0] == 684320
    assert row[2] == "TOR" and row[5] == "COL"
    assert row[6] == "home"
    assert row[7] == pytest.approx(3 + 2 / 3)
    assert row[8] == 6  # strikeouts


def test_load_pitcher_gamelogs_flow(monkeypatch):
    api = MagicMock()
    api.get_team_id_map.return_value = {141: "TOR", 115: "COL"}
    api.get_player_game_log.return_value = [
        {
            "date": "2024-04-13",
            "isHome": False,
            "game": {"gamePk": 1},
            "team": {"id": 141},
            "opponent": {"id": 115},
            "stat": {"inningsPitched": "5.0", "strikeOuts": 7},
        }
    ]
    monkeypatch.setattr(pitcher_logs, "StatsAPIWrapper", lambda *a, **k: api)
    conn, cursor = make_conn(fetch_results=[(684320, "P One")])
    monkeypatch.setattr(pitcher_logs.psycopg2, "connect", lambda **k: conn)
    with patch.object(pitcher_logs, "execute_values") as ev:
        n = pitcher_logs.load_pitcher_gamelogs([2024])
    assert n == 1
    rows = ev.call_args[0][2]
    assert rows[0][8] == 7  # strikeouts column


# ---------------------------------------------------------------------------
# load_batter_gamelogs
# ---------------------------------------------------------------------------
def test_batter_parse_split():
    split = {
        "date": "2024-05-01",
        "isHome": False,
        "game": {"gamePk": 5},
        "team": {"abbreviation": "KC"},
        "opponent": {"abbreviation": "DET"},
        "stat": {
            "atBats": 4,
            "hits": 2,
            "totalBases": 3,
            "runs": 1,
            "rbi": 2,
            "homeRuns": 0,
            "baseOnBalls": 1,
            "strikeOuts": 1,
            "plateAppearances": 5,
        },
    }
    row = batter_logs._parse_split(split, 677951, "B Witt", {})
    assert row[2] == "KC" and row[5] == "DET"
    assert row[6] == "away"
    assert row[8] == 2 and row[9] == 3  # hits, total_bases


def test_load_batter_gamelogs_flow(monkeypatch):
    api = MagicMock()
    api.get_team_id_map.return_value = {}
    api.get_player_game_log.return_value = [
        {
            "date": "2024-05-01",
            "isHome": True,
            "game": {"gamePk": 9},
            "team": {"abbreviation": "KC"},
            "opponent": {"abbreviation": "DET"},
            "stat": {"atBats": 4, "hits": 1, "totalBases": 1},
        }
    ]
    monkeypatch.setattr(batter_logs, "StatsAPIWrapper", lambda *a, **k: api)
    conn, _ = make_conn(fetch_results=[(677951, "B Witt")])
    monkeypatch.setattr(batter_logs.psycopg2, "connect", lambda **k: conn)
    with patch.object(batter_logs, "execute_values") as ev:
        n = batter_logs.load_batter_gamelogs([2024])
    assert n == 1
    rows = ev.call_args[0][2]
    assert rows[0][8] == 1  # hits


# ---------------------------------------------------------------------------
# calculate_team_stats
# ---------------------------------------------------------------------------
def test_calculate_team_season_stats(monkeypatch):
    games_conn, games_cur = make_conn()
    team_conn, _ = make_conn()
    # (team, games, avg_runs_scored, avg_runs_allowed, wins)
    games_cur.fetchall.return_value = [("NYY", 10, 5.0, 3.0, 7)]

    conns = iter([games_conn, team_conn])
    monkeypatch.setattr(team_stats.psycopg2, "connect", lambda **k: next(conns))
    with patch.object(team_stats, "execute_values") as ev:
        n = team_stats.calculate_team_season_stats([2024])
    assert n == 1
    rows = ev.call_args[0][2]
    assert rows[0][0] == "NYY"
    assert rows[0][5] == 2.0  # run differential 5-3
    assert rows[0][6] == 0.7  # win pct


def test_calculate_team_rolling_stats(monkeypatch):
    games_conn, games_cur = make_conn()
    team_conn, _ = make_conn()
    games_cur.fetchall.return_value = [("NYY", 10, 4.5, 3.5, "2024-07-01")]

    conns = iter([games_conn, team_conn])
    monkeypatch.setattr(team_stats.psycopg2, "connect", lambda **k: next(conns))
    with patch.object(team_stats, "execute_values") as ev:
        n = team_stats.calculate_team_rolling_stats([2024], window=10)
    assert n == 1
    rows = ev.call_args[0][2]
    assert rows[0][1] == 10  # window_size


def test_calculate_team_season_stats_empty(monkeypatch):
    games_conn, games_cur = make_conn()
    team_conn, _ = make_conn()
    games_cur.fetchall.return_value = []
    conns = iter([games_conn, team_conn])
    monkeypatch.setattr(team_stats.psycopg2, "connect", lambda **k: next(conns))
    assert team_stats.calculate_team_season_stats([2024]) == 0


# ---------------------------------------------------------------------------
# fetch_mlb_current_games
# ---------------------------------------------------------------------------
def test_preview_slate(monkeypatch):
    api = MagicMock()
    api.get_schedule.return_value = [
        {
            "gamePk": 1,
            "officialDate": "2024-07-01",
            "status": {"detailedState": "Scheduled"},
            "venue": {"name": "V"},
            "teams": {
                "home": {
                    "team": {"abbreviation": "TOR"},
                    "probablePitcher": {"id": 1, "fullName": "P"},
                },
                "away": {"team": {"abbreviation": "BOS"}},
            },
        }
    ]
    monkeypatch.setattr(current_games, "StatsAPIWrapper", lambda *a, **k: api)
    slate = current_games.preview_slate("2024-07-01")
    assert len(slate) == 1
    assert slate[0]["home_team"] == "TOR"
    assert slate[0]["probables"] == [{"team": "TOR", "pitcher": "P"}]


# ---------------------------------------------------------------------------
# backfill_season_gamelogs
# ---------------------------------------------------------------------------
def test_backfill_both_roles(monkeypatch):
    monkeypatch.setattr(backfill, "load_pitcher_gamelogs", lambda seasons: 11)
    monkeypatch.setattr(backfill, "load_batter_gamelogs", lambda seasons: 22)
    monkeypatch.setattr(backfill.time, "sleep", lambda *a: None)
    counts = backfill.backfill([2024], role="both")
    assert counts == {"pitchers": 11, "batters": 22}


def test_backfill_pitchers_only(monkeypatch):
    called = {"bat": False}
    monkeypatch.setattr(backfill, "load_pitcher_gamelogs", lambda seasons: 5)

    def _bat(seasons):
        called["bat"] = True
        return 0

    monkeypatch.setattr(backfill, "load_batter_gamelogs", _bat)
    monkeypatch.setattr(backfill.time, "sleep", lambda *a: None)
    counts = backfill.backfill([2024], role="pitchers")
    assert counts == {"pitchers": 5, "batters": 0}
    assert called["bat"] is False
