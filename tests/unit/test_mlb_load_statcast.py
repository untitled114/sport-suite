"""
Unit tests for mlb.scripts.loaders.load_statcast.

The StatcastWrapper (pybaseball/HTTP) and psycopg2 (DB) are fully mocked. No
network or DB access occurs. Pitch frames are tiny, hand-built, clearly
fake-shaped, and only exercise the aggregate->upsert plumbing - never treated as
real stat data.
"""

from unittest.mock import MagicMock, patch

import pandas as pd

import mlb.scripts.loaders.load_statcast as ls


# ---------------------------------------------------------------------------
# _none
# ---------------------------------------------------------------------------
def test_none_passthrough_and_nan():
    assert ls._none(5) == 5
    assert ls._none(None) is None
    assert ls._none(float("nan")) is None


def test_none_unhashable_falls_through():
    # pd.isna on a list raises ValueError -> caught -> value returned as-is.
    val = [1, 2]
    assert ls._none(val) is val


# ---------------------------------------------------------------------------
# _rows_from_df
# ---------------------------------------------------------------------------
def test_rows_from_df_empty():
    assert ls._rows_from_df(None, ls.PITCHER_COLS) == []
    assert ls._rows_from_df(pd.DataFrame(), ls.PITCHER_COLS) == []


def test_rows_from_df_orders_columns_and_nan_to_none():
    df = pd.DataFrame(
        [
            {
                "player_id": 100,
                "game_id": 9001,
                "game_date": "2024-07-01",
                "pitches": 10,
                "swinging_strikes": 3,
                "called_strikes": 2,
                "swstr_pct": 0.3,
                "csw_pct": 0.5,
                "whiff_pct": float("nan"),  # -> None
                "chase_pct": 0.5,
                "avg_fastball_velo": 95.0,
                "putaway_pct": None,
                "strikeouts": 1,
                "batters_faced": 2,
            }
        ]
    )
    rows = ls._rows_from_df(df, ls.PITCHER_COLS)
    assert len(rows) == 1
    row = rows[0]
    assert row[0] == 100 and row[1] == 9001
    assert row[8] is None  # whiff_pct NaN -> None


# ---------------------------------------------------------------------------
# _upsert_pitchers / _upsert_batters
# ---------------------------------------------------------------------------
def test_upsert_pitchers_empty_returns_zero():
    conn = MagicMock()
    assert ls._upsert_pitchers(conn, []) == 0
    conn.cursor.assert_not_called()


def test_upsert_batters_empty_returns_zero():
    conn = MagicMock()
    assert ls._upsert_batters(conn, []) == 0
    conn.cursor.assert_not_called()


def test_upsert_pitchers_executes_and_commits():
    conn = MagicMock()
    cur = MagicMock()
    conn.cursor.return_value = cur
    rows = [(1,) * 14]
    with patch.object(ls, "execute_values") as ev:
        n = ls._upsert_pitchers(conn, rows)
    assert n == 1
    ev.assert_called_once()
    assert "statcast_pitcher_games" in ev.call_args[0][1]
    conn.commit.assert_called_once()
    cur.close.assert_called_once()


def test_upsert_batters_executes_and_commits():
    conn = MagicMock()
    cur = MagicMock()
    conn.cursor.return_value = cur
    rows = [(1,) * 15]
    with patch.object(ls, "execute_values") as ev:
        n = ls._upsert_batters(conn, rows)
    assert n == 1
    ev.assert_called_once()
    assert "statcast_batter_games" in ev.call_args[0][1]
    conn.commit.assert_called_once()
    cur.close.assert_called_once()


# ---------------------------------------------------------------------------
# _season_range
# ---------------------------------------------------------------------------
def test_season_range():
    assert ls._season_range(2024) == ("2024-01-01", "2024-12-31")


# ---------------------------------------------------------------------------
# load_statcast - injected wrapper + conn (owns_conn False)
# ---------------------------------------------------------------------------
def _fake_pitcher_df():
    return pd.DataFrame([{c: (100 if c == "player_id" else 0) for c in ls.PITCHER_COLS}])


def _fake_batter_df():
    return pd.DataFrame([{c: (500 if c == "player_id" else 0) for c in ls.BATTER_COLS}])


def test_load_statcast_injected_conn():
    wrapper = MagicMock()
    wrapper.fetch_range.return_value = pd.DataFrame({"x": [1, 2, 3]})
    wrapper.aggregate_pitchers.return_value = _fake_pitcher_df()
    wrapper.aggregate_batters.return_value = _fake_batter_df()
    conn = MagicMock()
    conn.cursor.return_value = MagicMock()

    with patch.object(ls, "execute_values"):
        p, b = ls.load_statcast("2024-07-01", "2024-07-31", wrapper=wrapper, conn=conn)

    assert p == 1 and b == 1
    # injected conn must NOT be closed by the loader
    conn.close.assert_not_called()


def test_load_statcast_handles_none_raw():
    wrapper = MagicMock()
    wrapper.fetch_range.return_value = None  # n_pitches = 0 branch
    wrapper.aggregate_pitchers.return_value = pd.DataFrame()
    wrapper.aggregate_batters.return_value = pd.DataFrame()
    conn = MagicMock()

    with patch.object(ls, "execute_values"):
        p, b = ls.load_statcast("2024-07-01", "2024-07-31", wrapper=wrapper, conn=conn)

    assert p == 0 and b == 0


def test_load_statcast_owns_conn_opens_and_closes(monkeypatch):
    wrapper = MagicMock()
    wrapper.fetch_range.return_value = pd.DataFrame()
    wrapper.aggregate_pitchers.return_value = pd.DataFrame()
    wrapper.aggregate_batters.return_value = pd.DataFrame()
    conn = MagicMock()
    monkeypatch.setattr(ls.psycopg2, "connect", lambda **k: conn)

    with patch.object(ls, "execute_values"):
        p, b = ls.load_statcast("2024-07-01", "2024-07-31", wrapper=wrapper)

    assert (p, b) == (0, 0)
    conn.close.assert_called_once()


def test_load_statcast_default_wrapper(monkeypatch):
    """When no wrapper is injected, a real StatcastWrapper is constructed."""
    made = {}

    class FakeWrapper:
        def __init__(self):
            made["created"] = True

        def fetch_range(self, s, e):
            return pd.DataFrame()

        def aggregate_pitchers(self, raw):
            return pd.DataFrame()

        def aggregate_batters(self, raw):
            return pd.DataFrame()

    monkeypatch.setattr(ls, "StatcastWrapper", FakeWrapper)
    conn = MagicMock()
    with patch.object(ls, "execute_values"):
        ls.load_statcast("2024-07-01", "2024-07-31", conn=conn)
    assert made.get("created") is True


# ---------------------------------------------------------------------------
# main() - three arg branches
# ---------------------------------------------------------------------------
def test_main_start_end(monkeypatch):
    captured = {}

    def fake_load(start, end):
        captured["range"] = (start, end)
        return (2, 3)

    monkeypatch.setattr(ls, "load_statcast", fake_load)
    monkeypatch.setattr(ls.sys, "argv", ["prog", "--start", "2024-07-01", "--end", "2024-07-05"])
    ls.main()
    assert captured["range"] == ("2024-07-01", "2024-07-05")


def test_main_seasons(monkeypatch):
    ranges = []
    monkeypatch.setattr(ls, "load_statcast", lambda s, e: ranges.append((s, e)) or (1, 1))
    monkeypatch.setattr(ls.sys, "argv", ["prog", "--season", "2023", "2024"])
    ls.main()
    assert ranges == [("2023-01-01", "2023-12-31"), ("2024-01-01", "2024-12-31")]


def test_main_default_current_season(monkeypatch):
    ranges = []
    monkeypatch.setattr(ls, "load_statcast", lambda s, e: ranges.append((s, e)) or (0, 0))
    monkeypatch.setattr(ls.sys, "argv", ["prog"])
    import mlb.scripts.utilities.statsapi_wrapper as sw

    monkeypatch.setattr(sw, "get_current_season", lambda: 2099)
    ls.main()
    assert ranges == [("2099-01-01", "2099-12-31")]
