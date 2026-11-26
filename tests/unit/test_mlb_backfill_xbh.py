"""
Unit tests for mlb.scripts.loaders.backfill_batter_xbh.

StatsAPIWrapper (HTTP) and psycopg2 (DB) are mocked. The payloads are the smallest shapes
that exercise the parsing and the SQL parameters.
"""

from unittest.mock import MagicMock

import pytest

import mlb.scripts.loaders.backfill_batter_xbh as xbh


def make_conn(fetchall=None):
    conn = MagicMock()
    cursor = MagicMock()
    cursor.fetchall.return_value = fetchall or []
    cursor.rowcount = -1
    conn.cursor.return_value = cursor
    return conn, cursor


def test_int_helper():
    assert xbh._int(3) == 3
    assert xbh._int("4") == 4
    assert xbh._int(None) is None
    assert xbh._int("x") is None


def test_updates_for_player_parses_and_defaults_components():
    api = MagicMock()
    api.get_player_game_log.return_value = [
        {
            "stat": {
                "doubles": 2,
                "triples": 1,
                "hitByPitch": 1,
                "intentionalWalks": 0,
                "sacFlies": 1,
            },
            "game": {"gamePk": 100},
        },
        # Missing components default to 0 (a clean line legitimately has 0).
        {"stat": {}, "game": {"gamePk": 101}},
        # No gamePk -> skipped entirely (never invent a game).
        {"stat": {"doubles": 5}, "game": {}},
    ]
    updates = xbh._xbh_updates_for_player(api, 555, [2024])
    assert updates == [
        (2, 1, 1, 0, 1, 555, 100),
        (0, 0, 0, 0, 0, 555, 101),
    ]


def test_updates_handles_fetch_error():
    api = MagicMock()
    api.get_player_game_log.side_effect = ValueError("boom")
    assert xbh._xbh_updates_for_player(api, 555, [2024]) == []


def test_backfill_executes_updates(monkeypatch):
    api = MagicMock()
    api.get_player_game_log.return_value = [
        {"stat": {"doubles": 1, "triples": 0}, "game": {"gamePk": 200}},
    ]
    monkeypatch.setattr(xbh, "StatsAPIWrapper", lambda *a, **k: api)
    conn, cursor = make_conn(fetchall=[(555, "Slugger")])
    monkeypatch.setattr(xbh.psycopg2, "connect", lambda **k: conn)

    n = xbh.backfill_batter_xbh([2024])
    assert n == 1  # rowcount=-1 -> falls back to len(updates)
    cursor.executemany.assert_called_once()
    assert cursor.executemany.call_args[0][1] == [(1, 0, 0, 0, 0, 555, 200)]


def test_backfill_uses_rowcount(monkeypatch):
    api = MagicMock()
    api.get_player_game_log.return_value = [
        {"stat": {"doubles": 1}, "game": {"gamePk": 200}},
    ]
    monkeypatch.setattr(xbh, "StatsAPIWrapper", lambda *a, **k: api)
    conn, cursor = make_conn(fetchall=[(555, "Slugger")])
    cursor.rowcount = 1
    monkeypatch.setattr(xbh.psycopg2, "connect", lambda **k: conn)
    assert xbh.backfill_batter_xbh([2024]) == 1


def test_backfill_single_player_no_updates(monkeypatch):
    api = MagicMock()
    api.get_player_game_log.return_value = []
    monkeypatch.setattr(xbh, "StatsAPIWrapper", lambda *a, **k: api)
    conn, cursor = make_conn(fetchall=[(555, "Slugger")])
    monkeypatch.setattr(xbh.psycopg2, "connect", lambda **k: conn)
    assert xbh.backfill_batter_xbh([2024], player_id=555) == 0
    cursor.executemany.assert_not_called()


def test_backfill_db_error_rolls_back(monkeypatch):
    api = MagicMock()
    api.get_player_game_log.return_value = [
        {"stat": {"doubles": 1}, "game": {"gamePk": 200}},
    ]
    monkeypatch.setattr(xbh, "StatsAPIWrapper", lambda *a, **k: api)
    conn, cursor = make_conn(fetchall=[(555, "Slugger")])
    cursor.executemany.side_effect = xbh.psycopg2.Error("db down")
    monkeypatch.setattr(xbh.psycopg2, "connect", lambda **k: conn)
    assert xbh.backfill_batter_xbh([2024]) == 0
    conn.rollback.assert_called_once()


def test_main(monkeypatch):
    called = {}
    monkeypatch.setattr(
        xbh, "backfill_batter_xbh", lambda s, player_id=None: called.update(s=s, p=player_id)
    )
    monkeypatch.setattr(xbh.sys, "argv", ["x", "--season", "2024", "--player-id", "9"])
    xbh.main()
    assert called == {"s": [2024], "p": 9}


def test_main_defaults_to_current_season(monkeypatch):
    called = {}
    monkeypatch.setattr(xbh, "backfill_batter_xbh", lambda s, player_id=None: called.update(s=s))
    monkeypatch.setattr(xbh, "get_current_season", lambda: 2030)
    monkeypatch.setattr(xbh.sys, "argv", ["x"])
    xbh.main()
    assert called == {"s": [2030]}


def test_main_raises(monkeypatch):
    monkeypatch.setattr(xbh, "backfill_batter_xbh", MagicMock(side_effect=ValueError("nope")))
    monkeypatch.setattr(xbh, "get_current_season", lambda: 2030)
    monkeypatch.setattr(xbh.sys, "argv", ["x"])
    with pytest.raises(ValueError):
        xbh.main()
