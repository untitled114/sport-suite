"""
Unit tests for the sabermetric / volume backfill loaders.

StatsAPIWrapper (HTTP) and psycopg2 (DB) are mocked: no network or database access. The
payloads are the smallest shapes that exercise the parsing and the SQL parameters.

Loaders under test:
    mlb.scripts.loaders.backfill_pitcher_hr_allowed
    mlb.scripts.loaders.backfill_lineup_slots
"""

from datetime import date
from unittest.mock import MagicMock, patch

import pytest

import mlb.scripts.loaders.backfill_lineup_slots as lineup
import mlb.scripts.loaders.backfill_pitcher_hr_allowed as hr


def make_conn(fetchall=None):
    """Mock psycopg2 connection whose cursor returns fetchall rows."""
    conn = MagicMock()
    cursor = MagicMock()
    cursor.fetchall.return_value = fetchall or []
    cursor.rowcount = -1
    conn.cursor.return_value = cursor
    return conn, cursor


# =========================================================================== #
# backfill_pitcher_hr_allowed
# =========================================================================== #
def test_hr_int_helper():
    assert hr._int(3) == 3
    assert hr._int("4") == 4
    assert hr._int(None) is None
    assert hr._int("x") is None


def test_hr_updates_for_player_parses_splits():
    api = MagicMock()
    api.get_player_game_log.return_value = [
        {"stat": {"homeRuns": 2}, "game": {"gamePk": 100}},
        {"stat": {"homeRuns": 0}, "game": {"gamePk": 101}},
        {"stat": {}, "game": {"gamePk": 102}},  # no HR -> skipped
        {"stat": {"homeRuns": 1}, "game": {}},  # no gamePk -> skipped
    ]
    updates = hr._hr_updates_for_player(api, 555, [2024])
    assert updates == [(2, 555, 100), (0, 555, 101)]


def test_hr_updates_handles_fetch_error():
    api = MagicMock()
    api.get_player_game_log.side_effect = ValueError("boom")
    assert hr._hr_updates_for_player(api, 555, [2024]) == []


def test_hr_backfill_executes_updates(monkeypatch):
    api = MagicMock()
    api.get_player_game_log.return_value = [
        {"stat": {"homeRuns": 1}, "game": {"gamePk": 200}},
    ]
    monkeypatch.setattr(hr, "StatsAPIWrapper", lambda *a, **k: api)
    conn, cursor = make_conn(fetchall=[(555, "Ace")])
    monkeypatch.setattr(hr.psycopg2, "connect", lambda **k: conn)

    n = hr.backfill_pitcher_hr_allowed([2024])
    # rowcount=-1 path -> falls back to len(updates)=1
    assert n == 1
    cursor.executemany.assert_called_once()
    assert cursor.executemany.call_args[0][1] == [(1, 555, 200)]


def test_hr_backfill_uses_rowcount(monkeypatch):
    api = MagicMock()
    api.get_player_game_log.return_value = [
        {"stat": {"homeRuns": 1}, "game": {"gamePk": 200}},
    ]
    monkeypatch.setattr(hr, "StatsAPIWrapper", lambda *a, **k: api)
    conn, cursor = make_conn(fetchall=[(555, "Ace")])
    cursor.rowcount = 1
    monkeypatch.setattr(hr.psycopg2, "connect", lambda **k: conn)
    assert hr.backfill_pitcher_hr_allowed([2024]) == 1


def test_hr_backfill_single_player(monkeypatch):
    api = MagicMock()
    api.get_player_game_log.return_value = []
    monkeypatch.setattr(hr, "StatsAPIWrapper", lambda *a, **k: api)
    conn, cursor = make_conn(fetchall=[(555, "Ace")])
    monkeypatch.setattr(hr.psycopg2, "connect", lambda **k: conn)
    # no updates -> 0, executemany never called
    assert hr.backfill_pitcher_hr_allowed([2024], player_id=555) == 0
    cursor.executemany.assert_not_called()


def test_hr_backfill_db_error_rolls_back(monkeypatch):
    api = MagicMock()
    api.get_player_game_log.return_value = [
        {"stat": {"homeRuns": 1}, "game": {"gamePk": 200}},
    ]
    monkeypatch.setattr(hr, "StatsAPIWrapper", lambda *a, **k: api)
    conn, cursor = make_conn(fetchall=[(555, "Ace")])
    cursor.executemany.side_effect = hr.psycopg2.Error("db down")
    monkeypatch.setattr(hr.psycopg2, "connect", lambda **k: conn)
    n = hr.backfill_pitcher_hr_allowed([2024])
    assert n == 0
    conn.rollback.assert_called_once()


def test_hr_main(monkeypatch):
    called = {}
    monkeypatch.setattr(
        hr, "backfill_pitcher_hr_allowed", lambda s, player_id=None: called.update(s=s, p=player_id)
    )
    monkeypatch.setattr(hr.sys, "argv", ["x", "--season", "2024", "--player-id", "9"])
    hr.main()
    assert called == {"s": [2024], "p": 9}


def test_hr_main_defaults_to_current_season(monkeypatch):
    called = {}
    monkeypatch.setattr(
        hr, "backfill_pitcher_hr_allowed", lambda s, player_id=None: called.update(s=s)
    )
    monkeypatch.setattr(hr, "get_current_season", lambda: 2030)
    monkeypatch.setattr(hr.sys, "argv", ["x"])
    hr.main()
    assert called == {"s": [2030]}


def test_hr_main_raises(monkeypatch):
    monkeypatch.setattr(
        hr,
        "backfill_pitcher_hr_allowed",
        MagicMock(side_effect=ValueError("nope")),
    )
    monkeypatch.setattr(hr, "get_current_season", lambda: 2030)
    monkeypatch.setattr(hr.sys, "argv", ["x"])
    with pytest.raises(ValueError):
        hr.main()


# =========================================================================== #
# backfill_lineup_slots
# =========================================================================== #
def test_parse_batting_order():
    assert lineup._parse_batting_order("100") == 1
    assert lineup._parse_batting_order("201") == 2
    assert lineup._parse_batting_order("900") == 9
    assert lineup._parse_batting_order(None) is None
    assert lineup._parse_batting_order("abc") is None
    assert lineup._parse_batting_order("1000") is None  # slot 10 -> out of range
    assert lineup._parse_batting_order("0") is None


def test_slots_from_boxscore():
    box = {
        "teams": {
            "home": {
                "players": {
                    "ID1": {"person": {"id": 1}, "battingOrder": "100"},
                    "ID2": {"person": {"id": 2}, "battingOrder": "201"},
                    "IDP": {"person": {"id": 3}, "battingOrder": None},  # skip
                }
            },
            "away": {
                "players": {
                    "ID4": {"person": {"id": 4}, "battingOrder": "300"},
                    "IDX": {"person": {}, "battingOrder": "400"},  # no id -> skip
                }
            },
        }
    }
    rows = lineup._slots_from_boxscore(box, 999, "2024-07-01")
    assert (1, 999, "2024-07-01", 1) in rows
    assert (2, 999, "2024-07-01", 2) in rows
    assert (4, 999, "2024-07-01", 3) in rows
    assert len(rows) == 3


def test_slots_from_empty_boxscore():
    assert lineup._slots_from_boxscore({}, 1, "2024-07-01") == []
    assert lineup._slots_from_boxscore(None, 1, "2024-07-01") == []


def test_target_games_all_seasons():
    conn, cursor = make_conn(fetchall=[(10, date(2024, 7, 1)), (11, "2024-07-02")])
    games = lineup._target_games(conn, [2024], None)
    assert games == [(10, "2024-07-01"), (11, "2024-07-02")]


def test_target_games_single_game():
    conn, cursor = make_conn(fetchall=[(10, date(2024, 7, 1))])
    games = lineup._target_games(conn, [2024], 10)
    assert games == [(10, "2024-07-01")]
    # single-game branch queries by game_id
    assert "WHERE game_id = %s" in cursor.execute.call_args[0][0]


def test_lineup_backfill_inserts(monkeypatch):
    api = MagicMock()
    api.get_boxscore.return_value = {
        "teams": {"home": {"players": {"ID1": {"person": {"id": 1}, "battingOrder": "100"}}}}
    }
    monkeypatch.setattr(lineup, "StatsAPIWrapper", lambda *a, **k: api)
    conn, cursor = make_conn(fetchall=[(10, date(2024, 7, 1))])
    monkeypatch.setattr(lineup.psycopg2, "connect", lambda **k: conn)

    with patch.object(lineup, "execute_values") as ev:
        n = lineup.backfill_lineup_slots([2024])
    assert n == 1
    rows = ev.call_args[0][2]
    assert rows == [(1, 10, "2024-07-01", 1)]


def test_lineup_backfill_skips_empty_box(monkeypatch):
    api = MagicMock()
    api.get_boxscore.return_value = {"teams": {}}
    monkeypatch.setattr(lineup, "StatsAPIWrapper", lambda *a, **k: api)
    conn, _ = make_conn(fetchall=[(10, date(2024, 7, 1))])
    monkeypatch.setattr(lineup.psycopg2, "connect", lambda **k: conn)
    with patch.object(lineup, "execute_values") as ev:
        n = lineup.backfill_lineup_slots([2024])
    assert n == 0
    ev.assert_not_called()


def test_lineup_backfill_boxscore_error(monkeypatch):
    api = MagicMock()
    api.get_boxscore.side_effect = ValueError("404")
    monkeypatch.setattr(lineup, "StatsAPIWrapper", lambda *a, **k: api)
    conn, _ = make_conn(fetchall=[(10, date(2024, 7, 1))])
    monkeypatch.setattr(lineup.psycopg2, "connect", lambda **k: conn)
    assert lineup.backfill_lineup_slots([2024]) == 0


def test_lineup_backfill_db_error_rolls_back(monkeypatch):
    api = MagicMock()
    api.get_boxscore.return_value = {
        "teams": {"home": {"players": {"ID1": {"person": {"id": 1}, "battingOrder": "100"}}}}
    }
    monkeypatch.setattr(lineup, "StatsAPIWrapper", lambda *a, **k: api)
    conn, cursor = make_conn(fetchall=[(10, date(2024, 7, 1))])
    monkeypatch.setattr(lineup.psycopg2, "connect", lambda **k: conn)
    with patch.object(lineup, "execute_values", side_effect=lineup.psycopg2.Error("x")):
        n = lineup.backfill_lineup_slots([2024])
    assert n == 0
    conn.rollback.assert_called_once()


def test_lineup_progress_logging_many_games(monkeypatch):
    # Drive the idx % 50 / idx == len branch with >50 games.
    api = MagicMock()
    api.get_boxscore.return_value = {
        "teams": {"home": {"players": {"ID1": {"person": {"id": 1}, "battingOrder": "100"}}}}
    }
    monkeypatch.setattr(lineup, "StatsAPIWrapper", lambda *a, **k: api)
    games = [(i, date(2024, 7, 1)) for i in range(55)]
    conn, _ = make_conn(fetchall=games)
    monkeypatch.setattr(lineup.psycopg2, "connect", lambda **k: conn)
    with patch.object(lineup, "execute_values"):
        n = lineup.backfill_lineup_slots([2024])
    assert n == 55


def test_lineup_main(monkeypatch):
    called = {}
    monkeypatch.setattr(
        lineup, "backfill_lineup_slots", lambda s, game_id=None: called.update(s=s, g=game_id)
    )
    monkeypatch.setattr(lineup.sys, "argv", ["x", "--season", "2024", "--game-id", "7"])
    lineup.main()
    assert called == {"s": [2024], "g": 7}


def test_lineup_main_defaults(monkeypatch):
    called = {}
    monkeypatch.setattr(lineup, "backfill_lineup_slots", lambda s, game_id=None: called.update(s=s))
    monkeypatch.setattr(lineup, "get_current_season", lambda: 2030)
    monkeypatch.setattr(lineup.sys, "argv", ["x"])
    lineup.main()
    assert called == {"s": [2030]}


def test_lineup_main_raises(monkeypatch):
    monkeypatch.setattr(lineup, "backfill_lineup_slots", MagicMock(side_effect=ValueError("nope")))
    monkeypatch.setattr(lineup, "get_current_season", lambda: 2030)
    monkeypatch.setattr(lineup.sys, "argv", ["x"])
    with pytest.raises(ValueError):
        lineup.main()
