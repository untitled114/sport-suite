"""
The less-travelled branches of the MLB loaders, the stats API wrapper and the team-stats
calculators: empty responses, missing ids, skipped blocks, command-line paths.

HTTP (StatsAPIWrapper) and the database (psycopg2) are mocked: no network or database
access. The payloads are the minimal shapes the real calls return.
"""

from unittest.mock import MagicMock, patch

import psycopg2
import pytest

import mlb.scripts.loaders.calculate_team_stats as team_stats
import mlb.scripts.loaders.fetch_mlb_current_games as current_games
import mlb.scripts.loaders.load_batter_gamelogs as batter_logs
import mlb.scripts.loaders.load_mlb_games_incremental as games_inc
import mlb.scripts.loaders.load_mlb_players as players
import mlb.scripts.loaders.load_mlb_teams as teams
import mlb.scripts.loaders.load_pitcher_gamelogs as pitcher_logs
import mlb.scripts.utilities.statsapi_wrapper as sw


def make_conn(fetch_results=None):
    conn = MagicMock()
    cursor = MagicMock()
    cursor.fetchall.return_value = fetch_results or []
    cursor.fetchone.return_value = fetch_results[0] if fetch_results else None
    conn.cursor.return_value = cursor
    return conn, cursor


# ===========================================================================
# statsapi_wrapper._rate_limit (real sleep path)
# ===========================================================================
def test_rate_limit_sleeps_when_too_fast():
    api = sw.StatsAPIWrapper(requests_per_minute=60)  # min_delay = 1.0s
    api.last_request_time = sw.time.time()  # just made a request
    with patch.object(sw.time, "sleep") as slp:
        api._rate_limit()
    slp.assert_called_once()
    assert slp.call_args[0][0] > 0


def test_rate_limit_noop_when_disabled():
    api = sw.StatsAPIWrapper(requests_per_minute=0)
    with patch.object(sw.time, "sleep") as slp:
        api._rate_limit()
    slp.assert_not_called()


def test_rate_limit_no_sleep_when_enough_elapsed():
    api = sw.StatsAPIWrapper(requests_per_minute=60)  # min_delay = 1.0s
    api.last_request_time = 0.0  # long ago -> elapsed >= min_delay -> no sleep
    with patch.object(sw.time, "sleep") as slp:
        api._rate_limit()
    slp.assert_not_called()


# ===========================================================================
# load_mlb_teams - connect/close/main
# ===========================================================================
def test_teams_connect_success(monkeypatch):
    loader = teams.MLBTeamLoader()
    conn = MagicMock()
    monkeypatch.setattr(teams.psycopg2, "connect", lambda **k: conn)
    loader.connect()
    assert loader.conn is conn


def test_teams_connect_failure(monkeypatch):
    loader = teams.MLBTeamLoader()

    def boom(**k):
        raise psycopg2.Error("nope")

    monkeypatch.setattr(teams.psycopg2, "connect", boom)
    with pytest.raises(psycopg2.Error):
        loader.connect()


def test_teams_close():
    loader = teams.MLBTeamLoader()
    loader.conn = MagicMock()
    loader.close()
    loader.conn.close.assert_called_once()


def test_teams_close_no_conn():
    loader = teams.MLBTeamLoader()
    loader.conn = None
    assert loader.close() is None


def test_teams_main_success(monkeypatch):
    inst = MagicMock()
    monkeypatch.setattr(teams, "MLBTeamLoader", lambda: inst)
    monkeypatch.setattr(teams.sys, "argv", ["prog"])
    teams.main()
    inst.connect.assert_called_once()
    inst.load_teams.assert_called_once()
    inst.close.assert_called_once()


def test_teams_main_failure(monkeypatch):
    inst = MagicMock()
    inst.load_teams.side_effect = psycopg2.Error("fail")
    monkeypatch.setattr(teams, "MLBTeamLoader", lambda: inst)
    monkeypatch.setattr(teams.sys, "argv", ["prog"])
    with pytest.raises(psycopg2.Error):
        teams.main()
    inst.close.assert_called_once()


# ===========================================================================
# load_mlb_players - connect/close/team-id resolution/roster-fail/main
# ===========================================================================
def test_players_connect_success(monkeypatch):
    loader = players.MLBPlayerLoader()
    conn = MagicMock()
    monkeypatch.setattr(players.psycopg2, "connect", lambda **k: conn)
    loader.connect()
    assert loader.conn is conn


def test_players_connect_failure(monkeypatch):
    loader = players.MLBPlayerLoader()
    monkeypatch.setattr(
        players.psycopg2, "connect", lambda **k: (_ for _ in ()).throw(psycopg2.Error("x"))
    )
    with pytest.raises(psycopg2.Error):
        loader.connect()


def test_players_close():
    loader = players.MLBPlayerLoader()
    loader.conn = MagicMock()
    loader.close()
    loader.conn.close.assert_called_once()


def test_players_close_no_conn():
    loader = players.MLBPlayerLoader()
    loader.conn = None
    assert loader.close() is None


def test_players_team_ids_all_uses_map_once():
    loader = players.MLBPlayerLoader()
    loader.api = MagicMock()
    loader.api.get_team_id_map.return_value = {1: "A", 2: "B"}
    # first call populates the map
    assert sorted(loader._team_ids(None)) == [1, 2]
    # second call: map already populated -> no second API hit
    assert sorted(loader._team_ids(None)) == [1, 2]
    loader.api.get_team_id_map.assert_called_once()


def test_players_roster_fetch_failure_skips(monkeypatch):
    loader = players.MLBPlayerLoader()
    loader.conn, _ = make_conn()
    loader.api = MagicMock()
    loader.api.get_team_id_map.return_value = {147: "NYY"}
    loader.api.get_roster.side_effect = psycopg2.Error("roster down")
    with patch.object(players, "execute_values") as ev:
        counts = loader.load_rosters(team_id=147)
    assert counts == {"pitchers": 0, "batters": 0}
    ev.assert_not_called()


def test_players_main_success(monkeypatch):
    inst = MagicMock()
    inst.load_rosters.return_value = {"pitchers": 1, "batters": 2}
    monkeypatch.setattr(players, "MLBPlayerLoader", lambda: inst)
    monkeypatch.setattr(players.sys, "argv", ["prog", "--team-id", "147"])
    players.main()
    inst.load_rosters.assert_called_once_with(team_id=147)
    inst.close.assert_called_once()


def test_players_main_failure(monkeypatch):
    inst = MagicMock()
    inst.connect.side_effect = psycopg2.Error("conn fail")
    monkeypatch.setattr(players, "MLBPlayerLoader", lambda: inst)
    monkeypatch.setattr(players.sys, "argv", ["prog"])
    with pytest.raises(psycopg2.Error):
        players.main()
    inst.close.assert_called_once()


# ===========================================================================
# load_pitcher_gamelogs - profile ids / single-player / fetch-fail / no-rows /
# insert-fail / main
# ===========================================================================
def test_profile_player_ids():
    conn, cur = make_conn(fetch_results=[(1,), (2,)])
    assert pitcher_logs._profile_player_ids(conn) == [1, 2]
    cur.close.assert_called_once()


def test_pitcher_single_player(monkeypatch):
    api = MagicMock()
    api.get_team_id_map.return_value = {}
    api.get_player_game_log.return_value = [
        {
            "date": "2024-04-13",
            "isHome": True,
            "game": {"gamePk": 1},
            "team": {"abbreviation": "TOR"},
            "opponent": {"abbreviation": "COL"},
            "stat": {"inningsPitched": "5.0", "strikeOuts": 7},
        }
    ]
    monkeypatch.setattr(pitcher_logs, "StatsAPIWrapper", lambda *a, **k: api)
    conn, cur = make_conn(fetch_results=[("P One",)])
    monkeypatch.setattr(pitcher_logs.psycopg2, "connect", lambda **k: conn)
    with patch.object(pitcher_logs, "execute_values"):
        n = pitcher_logs.load_pitcher_gamelogs([2024], player_id=684320)
    assert n == 1


def test_pitcher_single_player_no_profile_row(monkeypatch):
    api = MagicMock()
    api.get_team_id_map.return_value = {}
    api.get_player_game_log.return_value = []  # no rows -> continue
    monkeypatch.setattr(pitcher_logs, "StatsAPIWrapper", lambda *a, **k: api)
    conn, _ = make_conn(fetch_results=None)  # fetchone -> None -> full_name None
    monkeypatch.setattr(pitcher_logs.psycopg2, "connect", lambda **k: conn)
    with patch.object(pitcher_logs, "execute_values") as ev:
        n = pitcher_logs.load_pitcher_gamelogs([2024], player_id=999)
    assert n == 0
    ev.assert_not_called()


def test_pitcher_fetch_failure_continues(monkeypatch):
    api = MagicMock()
    api.get_team_id_map.return_value = {}
    api.get_player_game_log.side_effect = ValueError("fetch boom")
    monkeypatch.setattr(pitcher_logs, "StatsAPIWrapper", lambda *a, **k: api)
    conn, _ = make_conn(fetch_results=[(1, "P One")])
    monkeypatch.setattr(pitcher_logs.psycopg2, "connect", lambda **k: conn)
    with patch.object(pitcher_logs, "execute_values") as ev:
        n = pitcher_logs.load_pitcher_gamelogs([2024])
    assert n == 0
    ev.assert_not_called()


def test_pitcher_insert_failure_rollback(monkeypatch):
    api = MagicMock()
    api.get_team_id_map.return_value = {}
    api.get_player_game_log.return_value = [
        {"date": "2024-04-13", "game": {"gamePk": 1}, "stat": {"strikeOuts": 5}}
    ]
    monkeypatch.setattr(pitcher_logs, "StatsAPIWrapper", lambda *a, **k: api)
    conn, _ = make_conn(fetch_results=[(1, "P One")])
    monkeypatch.setattr(pitcher_logs.psycopg2, "connect", lambda **k: conn)
    with patch.object(pitcher_logs, "execute_values", side_effect=psycopg2.Error("insert boom")):
        n = pitcher_logs.load_pitcher_gamelogs([2024])
    assert n == 0
    conn.rollback.assert_called_once()


def test_pitcher_main_default_season(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        pitcher_logs,
        "load_pitcher_gamelogs",
        lambda seasons, player_id=None: captured.update(seasons=seasons, pid=player_id),
    )
    monkeypatch.setattr(pitcher_logs, "get_current_season", lambda: 2099)
    monkeypatch.setattr(pitcher_logs.sys, "argv", ["prog"])
    pitcher_logs.main()
    assert captured["seasons"] == [2099] and captured["pid"] is None


def test_pitcher_main_failure(monkeypatch):
    monkeypatch.setattr(
        pitcher_logs,
        "load_pitcher_gamelogs",
        lambda *a, **k: (_ for _ in ()).throw(psycopg2.Error("fail")),
    )
    monkeypatch.setattr(pitcher_logs, "get_current_season", lambda: 2099)
    monkeypatch.setattr(pitcher_logs.sys, "argv", ["prog", "--season", "2024"])
    with pytest.raises(psycopg2.Error):
        pitcher_logs.main()


# ===========================================================================
# load_batter_gamelogs - single-player / fetch-fail / no-rows / insert-fail / main
# ===========================================================================
def test_batter_single_player(monkeypatch):
    api = MagicMock()
    api.get_team_id_map.return_value = {}
    api.get_player_game_log.return_value = [
        {
            "date": "2024-05-01",
            "isHome": False,
            "game": {"gamePk": 5},
            "team": {"abbreviation": "KC"},
            "opponent": {"abbreviation": "DET"},
            "stat": {"atBats": 4, "hits": 2},
        }
    ]
    monkeypatch.setattr(batter_logs, "StatsAPIWrapper", lambda *a, **k: api)
    conn, _ = make_conn(fetch_results=[("B Witt",)])
    monkeypatch.setattr(batter_logs.psycopg2, "connect", lambda **k: conn)
    with patch.object(batter_logs, "execute_values"):
        n = batter_logs.load_batter_gamelogs([2024], player_id=677951)
    assert n == 1


def test_batter_fetch_failure_continues(monkeypatch):
    api = MagicMock()
    api.get_team_id_map.return_value = {}
    api.get_player_game_log.side_effect = ValueError("boom")
    monkeypatch.setattr(batter_logs, "StatsAPIWrapper", lambda *a, **k: api)
    conn, _ = make_conn(fetch_results=[(1, "B Witt")])
    monkeypatch.setattr(batter_logs.psycopg2, "connect", lambda **k: conn)
    with patch.object(batter_logs, "execute_values") as ev:
        n = batter_logs.load_batter_gamelogs([2024])
    assert n == 0
    ev.assert_not_called()


def test_batter_insert_failure_rollback(monkeypatch):
    api = MagicMock()
    api.get_team_id_map.return_value = {}
    api.get_player_game_log.return_value = [
        {"date": "2024-05-01", "game": {"gamePk": 5}, "stat": {"hits": 1}}
    ]
    monkeypatch.setattr(batter_logs, "StatsAPIWrapper", lambda *a, **k: api)
    conn, _ = make_conn(fetch_results=[(1, "B Witt")])
    monkeypatch.setattr(batter_logs.psycopg2, "connect", lambda **k: conn)
    with patch.object(batter_logs, "execute_values", side_effect=psycopg2.Error("boom")):
        n = batter_logs.load_batter_gamelogs([2024])
    assert n == 0
    conn.rollback.assert_called_once()


def test_batter_main_default_season(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        batter_logs,
        "load_batter_gamelogs",
        lambda seasons, player_id=None: captured.update(seasons=seasons),
    )
    monkeypatch.setattr(batter_logs, "get_current_season", lambda: 2099)
    monkeypatch.setattr(batter_logs.sys, "argv", ["prog"])
    batter_logs.main()
    assert captured["seasons"] == [2099]


def test_batter_main_failure(monkeypatch):
    monkeypatch.setattr(
        batter_logs,
        "load_batter_gamelogs",
        lambda *a, **k: (_ for _ in ()).throw(ValueError("fail")),
    )
    monkeypatch.setattr(batter_logs, "get_current_season", lambda: 2099)
    monkeypatch.setattr(batter_logs.sys, "argv", ["prog"])
    with pytest.raises(ValueError):
        batter_logs.main()


# ===========================================================================
# load_mlb_games_incremental - skip-unusable / no-probables / error / main
# ===========================================================================
def test_load_games_skips_unusable_game(monkeypatch):
    api = MagicMock()
    api.get_schedule.return_value = [
        {},  # no gamePk -> _parse_game returns (None, []) -> skipped
        {
            "gamePk": 10,
            "officialDate": "2024-07-01",
            "status": {"detailedState": "Scheduled"},  # not final -> no probables
            "venue": {"name": "V"},
            "teams": {
                "home": {"team": {"abbreviation": "A"}},
                "away": {"team": {"abbreviation": "B"}},
            },
        },
    ]
    monkeypatch.setattr(games_inc, "StatsAPIWrapper", lambda *a, **k: api)
    conn, _ = make_conn()
    monkeypatch.setattr(games_inc.psycopg2, "connect", lambda **k: conn)
    with patch.object(games_inc, "execute_values") as ev:
        n = games_inc.load_games("2024-07-01", "2024-07-01")
    assert n == 1
    # only the games insert ran (no probable rows -> that block is skipped)
    assert ev.call_count == 1


def test_load_games_empty_schedule(monkeypatch):
    api = MagicMock()
    api.get_schedule.return_value = []  # no games -> games_rows empty
    monkeypatch.setattr(games_inc, "StatsAPIWrapper", lambda *a, **k: api)
    conn, _ = make_conn()
    monkeypatch.setattr(games_inc.psycopg2, "connect", lambda **k: conn)
    with patch.object(games_inc, "execute_values") as ev:
        n = games_inc.load_games("2024-07-01", "2024-07-01")
    assert n == 0
    ev.assert_not_called()


def test_load_games_with_probables(monkeypatch):
    api = MagicMock()
    api.get_schedule.return_value = [
        {
            "gamePk": 10,
            "officialDate": "2024-07-01",
            "status": {"detailedState": "Final"},
            "venue": {"name": "V"},
            "teams": {
                "home": {
                    "score": 1,
                    "team": {"abbreviation": "A"},
                    "probablePitcher": {"id": 5, "fullName": "P"},
                },
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
    assert ev.call_count == 2  # games + probables


def test_load_games_error_rolls_back(monkeypatch):
    api = MagicMock()
    api.get_schedule.side_effect = psycopg2.Error("schedule down")
    monkeypatch.setattr(games_inc, "StatsAPIWrapper", lambda *a, **k: api)
    conn, _ = make_conn()
    monkeypatch.setattr(games_inc.psycopg2, "connect", lambda **k: conn)
    with pytest.raises(psycopg2.Error):
        games_inc.load_games("2024-07-01", "2024-07-01")
    conn.rollback.assert_called_once()


def test_games_main_success(monkeypatch):
    monkeypatch.setattr(games_inc, "load_games", lambda s, e: 3)
    monkeypatch.setattr(games_inc.sys, "argv", ["prog", "--start", "2024-07-01"])
    assert games_inc.main() == 0


def test_games_main_failure(monkeypatch):
    monkeypatch.setattr(
        games_inc, "load_games", lambda s, e: (_ for _ in ()).throw(psycopg2.Error("x"))
    )
    monkeypatch.setattr(
        games_inc.sys, "argv", ["prog", "--start", "2024-07-01", "--end", "2024-07-02"]
    )
    assert games_inc.main() == 1


# ===========================================================================
# fetch_mlb_current_games - skip-unusable / main preview + insert
# ===========================================================================
def test_preview_slate_skips_unusable(monkeypatch):
    api = MagicMock()
    api.get_schedule.return_value = [{}]  # no gamePk -> grow None -> skipped
    monkeypatch.setattr(current_games, "StatsAPIWrapper", lambda *a, **k: api)
    assert current_games.preview_slate("2024-07-01") == []


def test_current_games_main_preview(monkeypatch):
    called = {}
    monkeypatch.setattr(current_games, "preview_slate", lambda d: called.update(preview=d) or [])
    monkeypatch.setattr(current_games.sys, "argv", ["prog", "--date", "2024-07-01"])
    current_games.main()
    assert called["preview"] == "2024-07-01"


def test_current_games_main_insert(monkeypatch):
    called = {}
    monkeypatch.setattr(current_games, "load_games", lambda a, b: called.update(load=(a, b)) or 4)
    monkeypatch.setattr(current_games.sys, "argv", ["prog", "--date", "2024-07-01", "--insert"])
    current_games.main()
    assert called["load"] == ("2024-07-01", "2024-07-01")


# ===========================================================================
# calculate_team_stats - error paths / rolling empty / defaults / main
# ===========================================================================
def test_season_stats_error_path(monkeypatch):
    games_conn, games_cur = make_conn()
    team_conn, _ = make_conn()
    games_cur.execute.side_effect = psycopg2.Error("query boom")
    conns = iter([games_conn, team_conn])
    monkeypatch.setattr(team_stats.psycopg2, "connect", lambda **k: next(conns))
    with pytest.raises(psycopg2.Error):
        team_stats.calculate_team_season_stats([2024])
    games_conn.close.assert_called_once()
    team_conn.close.assert_called_once()


def test_rolling_stats_empty(monkeypatch):
    games_conn, games_cur = make_conn()
    team_conn, _ = make_conn()
    games_cur.fetchall.return_value = []
    conns = iter([games_conn, team_conn])
    monkeypatch.setattr(team_stats.psycopg2, "connect", lambda **k: next(conns))
    assert team_stats.calculate_team_rolling_stats([2024]) == 0


def test_rolling_stats_error_path(monkeypatch):
    games_conn, games_cur = make_conn()
    team_conn, _ = make_conn()
    games_cur.execute.side_effect = psycopg2.Error("boom")
    conns = iter([games_conn, team_conn])
    monkeypatch.setattr(team_stats.psycopg2, "connect", lambda **k: next(conns))
    with pytest.raises(psycopg2.Error):
        team_stats.calculate_team_rolling_stats([2024])


def test_default_seasons():
    out = team_stats._default_seasons()
    assert isinstance(out, list) and len(out) == 1 and isinstance(out[0], int)


def test_team_stats_main(monkeypatch):
    calls = {}
    monkeypatch.setattr(
        team_stats,
        "calculate_team_season_stats",
        lambda seasons: calls.update(season=seasons),
    )
    monkeypatch.setattr(
        team_stats,
        "calculate_team_rolling_stats",
        lambda seasons, window: calls.update(rolling=(seasons, window)),
    )
    monkeypatch.setattr(team_stats.sys, "argv", ["prog", "--season", "2024", "--window", "5"])
    team_stats.main()
    assert calls["season"] == [2024]
    assert calls["rolling"] == ([2024], 5)


def test_team_stats_main_default_seasons(monkeypatch):
    calls = {}
    monkeypatch.setattr(team_stats, "calculate_team_season_stats", lambda seasons: None)
    monkeypatch.setattr(
        team_stats,
        "calculate_team_rolling_stats",
        lambda seasons, window: calls.update(seasons=seasons),
    )
    monkeypatch.setattr(team_stats, "_default_seasons", lambda: [2099])
    monkeypatch.setattr(team_stats.sys, "argv", ["prog"])
    team_stats.main()
    assert calls["seasons"] == [2099]
