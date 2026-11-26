"""
Unit tests for mlb.scripts.loaders.load_statcast_pitchmix.

The StatcastWrapper (pybaseball/HTTP) and psycopg2 (DB) are fully mocked. No
network or DB access. Aggregation helpers are patched/injected; tiny fake
frames exercise only the aggregate->upsert->rollup plumbing.
"""

from unittest.mock import MagicMock, patch

import pandas as pd

import mlb.scripts.loaders.load_statcast_pitchmix as ls


# ---------------------------------------------------------------------------
# _none / _rows_from_df
# ---------------------------------------------------------------------------
def test_none_passthrough_and_nan():
    assert ls._none(5) == 5
    assert ls._none(None) is None
    assert ls._none(float("nan")) is None


def test_none_unhashable_falls_through():
    val = [1, 2]
    assert ls._none(val) is val


def test_rows_from_df_empty():
    assert ls._rows_from_df(None, ls.PITCHMIX_COLS) == []
    assert ls._rows_from_df(pd.DataFrame(), ls.PITCHMIX_COLS) == []


def test_rows_from_df_orders_and_nan_to_none():
    df = pd.DataFrame(
        [
            {
                "player_id": 100,
                "game_id": 9001,
                "game_date": "2024-07-01",
                "num_pitch_types": 4,
                "fastball_velo": float("nan"),
                "fastball_spin": 2300.0,
                "breaking_spin": 2500.0,
                "zone_rate": 0.5,
                "first_strike_rate": 0.6,
                "fastball_whiff_rate": 0.2,
                "slider_whiff_rate": 0.3,
                "curve_whiff_rate": 0.3,
                "changeup_whiff_rate": 0.3,
            }
        ]
    )
    rows = ls._rows_from_df(df, ls.PITCHMIX_COLS)
    assert len(rows) == 1
    assert rows[0][0] == 100 and rows[0][1] == 9001
    assert rows[0][4] is None  # fastball_velo NaN -> None


# ---------------------------------------------------------------------------
# upsert helpers
# ---------------------------------------------------------------------------
def test_upsert_pitchmix_empty_returns_zero():
    conn = MagicMock()
    assert ls._upsert_pitchmix(conn, []) == 0
    conn.cursor.assert_not_called()


def test_upsert_battedball_empty_returns_zero():
    conn = MagicMock()
    assert ls._upsert_battedball(conn, []) == 0
    conn.cursor.assert_not_called()


def test_upsert_bvp_empty_returns_zero():
    conn = MagicMock()
    assert ls._upsert_bvp(conn, []) == 0
    conn.cursor.assert_not_called()


def test_upsert_pitchmix_executes_and_commits():
    conn = MagicMock()
    cur = MagicMock()
    conn.cursor.return_value = cur
    with patch.object(ls, "execute_values") as ev:
        n = ls._upsert_pitchmix(conn, [(1,) * len(ls.PITCHMIX_COLS)])
    assert n == 1
    ev.assert_called_once()
    assert "statcast_pitcher_pitchmix_games" in ev.call_args[0][1]
    conn.commit.assert_called_once()
    cur.close.assert_called_once()


def test_upsert_battedball_executes_and_commits():
    conn = MagicMock()
    cur = MagicMock()
    conn.cursor.return_value = cur
    with patch.object(ls, "execute_values") as ev:
        n = ls._upsert_battedball(conn, [(1,) * len(ls.BATTEDBALL_COLS)])
    assert n == 1
    ev.assert_called_once()
    assert "statcast_batter_battedball_games" in ev.call_args[0][1]
    conn.commit.assert_called_once()
    cur.close.assert_called_once()


def test_upsert_bvp_executes_and_commits():
    conn = MagicMock()
    cur = MagicMock()
    conn.cursor.return_value = cur
    with patch.object(ls, "execute_values") as ev:
        n = ls._upsert_bvp(conn, [(1,) * len(ls.BVP_COLS)])
    assert n == 1
    ev.assert_called_once()
    assert "bvp_encounters" in ev.call_args[0][1]
    conn.commit.assert_called_once()
    cur.close.assert_called_once()


# ---------------------------------------------------------------------------
# rebuild_matchup_history
# ---------------------------------------------------------------------------
def test_rebuild_matchup_history():
    conn = MagicMock()
    cur = MagicMock()
    cur.rowcount = 7
    conn.cursor.return_value = cur
    n = ls.rebuild_matchup_history(conn)
    assert n == 7
    # TRUNCATE then INSERT ... SELECT
    calls = [c.args[0] for c in cur.execute.call_args_list]
    assert any("TRUNCATE TABLE matchup_history" in c for c in calls)
    assert any("INSERT INTO matchup_history" in c and "FROM bvp_encounters" in c for c in calls)
    conn.commit.assert_called_once()
    cur.close.assert_called_once()


# ---------------------------------------------------------------------------
# _season_range
# ---------------------------------------------------------------------------
def test_season_range():
    assert ls._season_range(2024) == ("2024-01-01", "2024-12-31")


# ---------------------------------------------------------------------------
# load_statcast_pitchmix
# ---------------------------------------------------------------------------
def test_load_injected_conn():
    wrapper = MagicMock()
    wrapper.fetch_range.return_value = pd.DataFrame({"x": [1, 2, 3]})
    conn = MagicMock()
    conn.cursor.return_value = MagicMock()
    conn.cursor.return_value.rowcount = 2

    pm_df = pd.DataFrame([{c: (100 if c == "player_id" else 0) for c in ls.PITCHMIX_COLS}])
    bb_df = pd.DataFrame([{c: (500 if c == "player_id" else 0) for c in ls.BATTEDBALL_COLS}])
    bvp_df = pd.DataFrame(
        [{c: (1 if c in ("batter_id", "pitcher_id") else 0) for c in ls.BVP_COLS}]
    )

    with (
        patch.object(ls, "execute_values"),
        patch.object(ls, "aggregate_pitchmix", return_value=pm_df),
        patch.object(ls, "aggregate_battedball", return_value=bb_df),
        patch.object(ls, "aggregate_bvp_encounters", return_value=bvp_df),
    ):
        pm, bb, bvp = ls.load_statcast_pitchmix(
            "2024-07-01", "2024-07-31", wrapper=wrapper, conn=conn
        )

    assert (pm, bb, bvp) == (1, 1, 1)
    conn.close.assert_not_called()  # injected conn not closed


def test_load_handles_none_raw():
    wrapper = MagicMock()
    wrapper.fetch_range.return_value = None
    conn = MagicMock()
    conn.cursor.return_value.rowcount = 0

    with (
        patch.object(ls, "execute_values"),
        patch.object(ls, "aggregate_pitchmix", return_value=pd.DataFrame()),
        patch.object(ls, "aggregate_battedball", return_value=pd.DataFrame()),
        patch.object(ls, "aggregate_bvp_encounters", return_value=pd.DataFrame()),
    ):
        pm, bb, bvp = ls.load_statcast_pitchmix(
            "2024-07-01", "2024-07-31", wrapper=wrapper, conn=conn
        )

    assert (pm, bb, bvp) == (0, 0, 0)


def test_load_owns_conn_opens_and_closes(monkeypatch):
    wrapper = MagicMock()
    wrapper.fetch_range.return_value = pd.DataFrame()
    conn = MagicMock()
    conn.cursor.return_value.rowcount = 0
    monkeypatch.setattr(ls.psycopg2, "connect", lambda **k: conn)

    with (
        patch.object(ls, "execute_values"),
        patch.object(ls, "aggregate_pitchmix", return_value=pd.DataFrame()),
        patch.object(ls, "aggregate_battedball", return_value=pd.DataFrame()),
        patch.object(ls, "aggregate_bvp_encounters", return_value=pd.DataFrame()),
    ):
        pm, bb, bvp = ls.load_statcast_pitchmix("2024-07-01", "2024-07-31", wrapper=wrapper)

    assert (pm, bb, bvp) == (0, 0, 0)
    conn.close.assert_called_once()


def test_load_default_wrapper(monkeypatch):
    made = {}

    class FakeWrapper:
        def __init__(self):
            made["created"] = True

        def fetch_range(self, s, e):
            return pd.DataFrame()

    monkeypatch.setattr(ls, "StatcastWrapper", FakeWrapper)
    conn = MagicMock()
    conn.cursor.return_value.rowcount = 0
    with (
        patch.object(ls, "execute_values"),
        patch.object(ls, "aggregate_pitchmix", return_value=pd.DataFrame()),
        patch.object(ls, "aggregate_battedball", return_value=pd.DataFrame()),
        patch.object(ls, "aggregate_bvp_encounters", return_value=pd.DataFrame()),
    ):
        ls.load_statcast_pitchmix("2024-07-01", "2024-07-31", conn=conn)
    assert made.get("created") is True


# ---------------------------------------------------------------------------
# main()
# ---------------------------------------------------------------------------
def test_main_start_end(monkeypatch):
    captured = {}

    def fake_load(start, end):
        captured["range"] = (start, end)
        return (2, 3, 4)

    monkeypatch.setattr(ls, "load_statcast_pitchmix", fake_load)
    monkeypatch.setattr(ls.sys, "argv", ["prog", "--start", "2024-07-01", "--end", "2024-07-05"])
    ls.main()
    assert captured["range"] == ("2024-07-01", "2024-07-05")


def test_main_seasons(monkeypatch):
    ranges = []
    monkeypatch.setattr(
        ls, "load_statcast_pitchmix", lambda s, e: ranges.append((s, e)) or (1, 1, 1)
    )
    monkeypatch.setattr(ls.sys, "argv", ["prog", "--season", "2023", "2024"])
    ls.main()
    assert ranges == [("2023-01-01", "2023-12-31"), ("2024-01-01", "2024-12-31")]


def test_main_default_current_season(monkeypatch):
    ranges = []
    monkeypatch.setattr(
        ls, "load_statcast_pitchmix", lambda s, e: ranges.append((s, e)) or (0, 0, 0)
    )
    monkeypatch.setattr(ls.sys, "argv", ["prog"])
    import mlb.scripts.utilities.statsapi_wrapper as sw

    monkeypatch.setattr(sw, "get_current_season", lambda: 2099)
    ls.main()
    assert ranges == [("2099-01-01", "2099-12-31")]
