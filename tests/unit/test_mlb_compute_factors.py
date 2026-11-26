"""
Unit tests for the computation loaders:
    mlb.scripts.loaders.compute_park_factors
    mlb.scripts.loaders.compute_expected_pa

psycopg2 is mocked. The rows are the smallest sets that exercise the aggregation math and the
SQL parameters.
"""

from unittest.mock import MagicMock, patch

import pytest

import mlb.scripts.loaders.compute_expected_pa as epa
import mlb.scripts.loaders.compute_park_factors as pf


def make_conn(fetch_sequences):
    """Mock connection whose successive cursor.execute -> fetchall yields rows.

    ``fetch_sequences`` is a list; each cursor (one per .cursor() call) returns
    the next sequence's fetchall.
    """
    conn = MagicMock()
    seqs = list(fetch_sequences)

    def make_cursor():
        cur = MagicMock()
        cur.fetchall.return_value = seqs.pop(0) if seqs else []
        return cur

    conn.cursor.side_effect = make_cursor
    return conn


# =========================================================================== #
# compute_park_factors
# =========================================================================== #
def test_aggregate_drops_non_mlb_and_sums():
    runs_rows = [("LAD", 10, 1), ("SD", 6, 1), ("MEX", 99, 1)]  # MEX dropped
    bat_rows = [("LAD", 2, 9), ("SDP", 1, 7), ("MEX", 5, 5)]  # MEX dropped here too
    parks = pf._aggregate(runs_rows, bat_rows)
    assert "MEX" not in parks
    assert parks["LAD"] == {"games": 1.0, "runs": 10.0, "hr": 2.0, "hits": 9.0}
    # "SD" (runs) and "SDP" (bat) both normalize to SDP.
    assert parks["SDP"]["games"] == 1.0
    assert parks["SDP"]["runs"] == 6.0
    assert parks["SDP"]["hr"] == 1.0


def test_league_rate_and_compute_factors():
    parks = {
        "AAA": {"games": 2.0, "runs": 20.0, "hr": 4.0, "hits": 18.0},
        "BBB": {"games": 2.0, "runs": 10.0, "hr": 2.0, "hits": 12.0},
    }
    lg_runs = pf._league_rate(parks, "runs")  # 30/4 = 7.5
    assert lg_runs == pytest.approx(7.5)
    factors = pf.compute_factors(parks)
    # AAA runs/game = 10 vs lg 7.5 -> 1.3333
    assert factors["AAA"]["runs_factor"] == pytest.approx(round(10 / 7.5, 4))
    assert factors["AAA"]["n_games"] == 2


def test_league_rate_zero_games_returns_zero():
    assert pf._league_rate({}, "runs") == 0.0


def test_compute_factors_zero_games_park_skipped():
    parks = {
        "AAA": {"games": 2.0, "runs": 20.0, "hr": 4.0, "hits": 18.0},
        "ZZZ": {"games": 0.0, "runs": 0.0, "hr": 0.0, "hits": 0.0},
    }
    factors = pf.compute_factors(parks)
    assert "ZZZ" not in factors


def test_compute_factors_zero_league_rate_gives_none():
    # All HR zero -> lg_hr == 0 -> hr_factor None (not a fabricated 1.0).
    parks = {"AAA": {"games": 2.0, "runs": 20.0, "hr": 0.0, "hits": 18.0}}
    factors = pf.compute_factors(parks)
    assert factors["AAA"]["hr_factor"] is None
    assert factors["AAA"]["runs_factor"] is not None


def test_fetch_runs_rows_shape():
    conn = make_conn([[("LAD", 10), ("SD", 6)]])
    rows = pf._fetch_runs_rows(conn, 2024, 2026)
    assert rows == [("LAD", 10, 1), ("SD", 6, 1)]


def test_fetch_batting_rows_shape():
    conn = make_conn([[("LAD", 2, 9), ("SD", 1, 7)]])
    rows = pf._fetch_batting_rows(conn, 2024, 2026)
    assert rows == [("LAD", 2, 9), ("SD", 1, 7)]


def test_persist_empty_returns_zero():
    conn = MagicMock()
    assert pf._persist(conn, "w", {}) == 0


def test_persist_writes_rows():
    conn = MagicMock()
    factors = {"LAD": {"runs_factor": 1.1, "hr_factor": 1.2, "hits_factor": 0.9, "n_games": 5}}
    with patch.object(pf, "execute_values") as ev:
        n = pf._persist(conn, "w", factors)
    assert n == 1
    rows = ev.call_args[0][2]
    assert rows == [("LAD", "w", 1.1, 1.2, 0.9, 5)]
    conn.commit.assert_called_once()


def test_compute_park_factors_end_to_end(monkeypatch):
    conn = make_conn(
        [
            [("LAD", 10), ("SD", 6)],  # runs rows
            [("LAD", 2, 9), ("SD", 1, 7)],  # batting rows
        ]
    )
    monkeypatch.setattr(pf.psycopg2, "connect", lambda **k: conn)
    with patch.object(pf, "execute_values"):
        factors = pf.compute_park_factors(2024, 2026)
    assert "LAD" in factors and "SDP" in factors
    conn.close.assert_called_once()


def test_park_main(monkeypatch):
    called = {}
    monkeypatch.setattr(pf, "compute_park_factors", lambda s, e: called.update(s=s, e=e))
    monkeypatch.setattr(pf.sys, "argv", ["x", "--start", "2024", "--end", "2026"])
    pf.main()
    assert called == {"s": 2024, "e": 2026}


def test_park_main_defaults(monkeypatch):
    called = {}
    monkeypatch.setattr(pf, "compute_park_factors", lambda s, e: called.update(s=s, e=e))
    monkeypatch.setattr(pf, "get_current_season", lambda: 2026)
    monkeypatch.setattr(pf.sys, "argv", ["x"])
    pf.main()
    assert called == {"s": 2024, "e": 2026}


def test_park_main_raises(monkeypatch):
    monkeypatch.setattr(pf, "compute_park_factors", MagicMock(side_effect=ValueError("boom")))
    monkeypatch.setattr(pf, "get_current_season", lambda: 2026)
    monkeypatch.setattr(pf.sys, "argv", ["x"])
    with pytest.raises(ValueError):
        pf.main()


# =========================================================================== #
# compute_expected_pa
# =========================================================================== #
def test_fetch_slot_means_shape():
    conn = make_conn([[(1, 4.5, 100), (2, 4.4, 90)]])
    curve = epa._fetch_slot_means(conn, 2024, 2026)
    assert curve == {1: {"mean_pa": 4.5, "n_games": 100}, 2: {"mean_pa": 4.4, "n_games": 90}}


def test_epa_persist_empty_returns_zero():
    conn = MagicMock()
    assert epa._persist(conn, "w", {}) == 0


def test_epa_persist_writes_rows():
    conn = MagicMock()
    curve = {1: {"mean_pa": 4.5, "n_games": 100}}
    with patch.object(epa, "execute_values") as ev:
        n = epa._persist(conn, "w", curve)
    assert n == 1
    assert ev.call_args[0][2] == [(1, "w", 4.5, 100)]
    conn.commit.assert_called_once()


def test_compute_expected_pa_end_to_end(monkeypatch):
    conn = make_conn([[(1, 4.5, 100), (9, 2.9, 120)]])
    monkeypatch.setattr(epa.psycopg2, "connect", lambda **k: conn)
    with patch.object(epa, "execute_values"):
        curve = epa.compute_expected_pa(2024, 2026)
    assert set(curve) == {1, 9}
    conn.close.assert_called_once()


def test_epa_main(monkeypatch):
    called = {}
    monkeypatch.setattr(epa, "compute_expected_pa", lambda s, e: called.update(s=s, e=e))
    monkeypatch.setattr(epa.sys, "argv", ["x", "--start", "2024", "--end", "2026"])
    epa.main()
    assert called == {"s": 2024, "e": 2026}


def test_epa_main_defaults(monkeypatch):
    called = {}
    monkeypatch.setattr(epa, "compute_expected_pa", lambda s, e: called.update(s=s, e=e))
    monkeypatch.setattr(epa, "get_current_season", lambda: 2026)
    monkeypatch.setattr(epa.sys, "argv", ["x"])
    epa.main()
    assert called == {"s": 2024, "e": 2026}


def test_epa_main_raises(monkeypatch):
    monkeypatch.setattr(epa, "compute_expected_pa", MagicMock(side_effect=ValueError("boom")))
    monkeypatch.setattr(epa, "get_current_season", lambda: 2026)
    monkeypatch.setattr(epa.sys, "argv", ["x"])
    with pytest.raises(ValueError):
        epa.main()
