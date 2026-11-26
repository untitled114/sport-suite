"""
Unit tests for the MLB FeatureStore.
====================================
DB is mocked via a fake connection/cursor. No real DB access;
feature_values are tiny illustrative dicts to test (de)serialization
and SQL parameter wiring.
"""

import json
from datetime import date
from unittest.mock import MagicMock, patch

import pytest

import mlb.features.feature_store as fs_mod
from mlb.features.feature_store import FeatureStore, _json_default


class FakeCursor:
    def __init__(self, fetchone_result=None, fetchall_result=None):
        self._fetchone = fetchone_result
        self._fetchall = fetchall_result or []
        self.executed = []

    def execute(self, query, params=None):
        self.executed.append((query, params))

    def fetchone(self):
        return self._fetchone

    def fetchall(self):
        return self._fetchall

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class FakeConn:
    def __init__(self, cursor):
        self._cursor = cursor
        self.closed = False

    def cursor(self):
        return self._cursor

    def close(self):
        self.closed = True


@pytest.fixture
def store_and_cursor():
    cur = FakeCursor()
    conn = FakeConn(cur)
    store = FeatureStore(conn=conn)
    return store, cur


class TestWrite:
    def test_write_features_success(self, store_and_cursor):
        store, cur = store_and_cursor
        ok = store.write_features(
            "Gerrit Cole",
            "2026-04-10",
            "STRIKEOUTS",
            "mlb_v1",
            {"pitcher_ema_strikeouts_L10": 7.2},
            player_id=12,
        )
        assert ok is True
        query, params = cur.executed[-1]
        assert "INSERT INTO computed_features" in query
        # stat_type column carries the stat type value
        assert params[3] == "STRIKEOUTS"
        # feature_values serialized to JSON
        assert json.loads(params[5])["pitcher_ema_strikeouts_L10"] == 7.2

    def test_write_empty_skipped(self, store_and_cursor):
        store, cur = store_and_cursor
        assert store.write_features("p", "2026-04-10", "HITS", "mlb_v1", {}) is False
        assert cur.executed == []

    def test_write_batch(self, store_and_cursor):
        store, cur = store_and_cursor
        rows = [
            {
                "player_name": "A",
                "game_date": "2026-04-10",
                "stat_type": "HITS",
                "feature_values": {"batter_ema_target_L10": 1.1},
            },
            {
                "player_name": "B",
                "game_date": "2026-04-10",
                "stat_type": "TOTAL_BASES",
                "feature_values": {"batter_ema_target_L10": 1.9},
            },
            {
                "player_name": "C",
                "game_date": "2026-04-10",
                "stat_type": "HITS",
                "feature_values": {},
            },  # skipped (empty)
        ]
        written = store.write_batch(rows, "mlb_v1")
        assert written == 2

    def test_write_batch_empty(self, store_and_cursor):
        store, _ = store_and_cursor
        assert store.write_batch([], "mlb_v1") == 0


class TestRead:
    def test_read_player_features_json_string(self):
        cur = FakeCursor(fetchone_result=(json.dumps({"a": 1.0}),))
        store = FeatureStore(conn=FakeConn(cur))
        out = store.read_player_features("p", "2026-04-10", "HITS", "mlb_v1")
        assert out == {"a": 1.0}

    def test_read_player_features_dict(self):
        cur = FakeCursor(fetchone_result=({"a": 2.0},))
        store = FeatureStore(conn=FakeConn(cur))
        out = store.read_player_features("p", "2026-04-10", "HITS", "mlb_v1")
        assert out == {"a": 2.0}

    def test_read_player_features_missing(self):
        cur = FakeCursor(fetchone_result=None)
        store = FeatureStore(conn=FakeConn(cur))
        assert store.read_player_features("p", "2026-04-10", "HITS", "mlb_v1") is None

    def test_read_features_flattens_metadata(self):
        cur = FakeCursor(
            fetchall_result=[
                ("Cole", date(2026, 4, 10), "STRIKEOUTS", json.dumps({"k": 7.0})),
            ]
        )
        store = FeatureStore(conn=FakeConn(cur))
        rows = store.read_features("mlb_v1", start_date="2024-01-01", stat_type="STRIKEOUTS")
        assert rows[0]["player_name"] == "Cole"
        assert rows[0]["stat_type"] == "STRIKEOUTS"
        assert rows[0]["k"] == 7.0
        # stat type filter adds a stat_type condition
        query, params = cur.executed[-1]
        assert "stat_type = %s" in query

    def test_read_features_filters_appended(self):
        cur = FakeCursor(fetchall_result=[])
        store = FeatureStore(conn=FakeConn(cur))
        store.read_features("mlb_v1", start_date="2024-01-01", end_date="2026-10-01", limit=5)
        query, params = cur.executed[-1]
        assert "game_date >= %s" in query and "game_date <= %s" in query
        assert "LIMIT %s" in query
        assert params[-1] == 5


class TestMetadata:
    def test_get_feature_set(self):
        cur = FakeCursor(fetchone_result=("mlb_v1", "1.0", 70, ["a", "b"], "desc", True))
        store = FeatureStore(conn=FakeConn(cur))
        fs = store.get_feature_set("mlb_v1")
        assert fs["name"] == "mlb_v1"
        assert fs["feature_count"] == 70
        assert fs["is_active"] is True

    def test_list_feature_sets(self):
        cur = FakeCursor(fetchall_result=[("mlb_v1", "1.0", 70, True)])
        store = FeatureStore(conn=FakeConn(cur))
        sets = store.list_feature_sets()
        assert sets[0]["name"] == "mlb_v1"

    def test_get_coverage(self):
        cur = FakeCursor(fetchone_result=(123, date(2024, 3, 1), date(2026, 10, 1)))
        store = FeatureStore(conn=FakeConn(cur))
        cov = store.get_coverage("mlb_v1", stat_type="HITS")
        assert cov["count"] == 123
        assert cov["min_date"] == "2024-03-01"

    def test_update_feature_metadata(self):
        cur = FakeCursor()
        store = FeatureStore(conn=FakeConn(cur))
        ok = store.update_feature_metadata("mlb_v1", "park_factor", train_mean=100.0)
        assert ok is True
        assert "INSERT INTO feature_metadata" in cur.executed[-1][0]


class TestConnectionManagement:
    def test_get_conn_creates_when_none(self):
        fake = MagicMock()
        fake.closed = False
        with (
            patch.object(fs_mod.psycopg2, "connect", return_value=fake) as connect,
            patch.object(fs_mod, "get_features_db_config", return_value={"host": "x"}),
        ):
            store = FeatureStore()  # no external conn
            conn = store._get_conn()
        assert conn is fake
        assert fake.autocommit is True
        connect.assert_called_once()

    def test_get_conn_recreates_when_closed(self):
        closed = MagicMock()
        closed.closed = True
        fresh = MagicMock()
        fresh.closed = False
        store = FeatureStore(conn=closed)
        with (
            patch.object(fs_mod.psycopg2, "connect", return_value=fresh),
            patch.object(fs_mod, "get_features_db_config", return_value={}),
        ):
            assert store._get_conn() is fresh

    def test_close_internal_conn(self):
        fake = MagicMock()
        fake.closed = False
        with (
            patch.object(fs_mod.psycopg2, "connect", return_value=fake),
            patch.object(fs_mod, "get_features_db_config", return_value={}),
        ):
            store = FeatureStore()
            store._get_conn()
            store.close()
        fake.close.assert_called_once()

    def test_close_does_not_close_external(self):
        ext = MagicMock()
        ext.closed = False
        store = FeatureStore(conn=ext)
        store.close()
        ext.close.assert_not_called()


class TestErrorPaths:
    def _failing_store(self):
        conn = MagicMock()
        conn.closed = False
        conn.cursor.side_effect = RuntimeError("db down")
        return FeatureStore(conn=conn)

    def test_write_features_error_returns_false(self):
        assert (
            self._failing_store().write_features("p", "2026-04-10", "HITS", "mlb_v1", {"a": 1.0})
            is False
        )

    def test_write_batch_error_returns_partial(self):
        # cursor itself fails -> 0 written, exception swallowed
        assert (
            self._failing_store().write_batch(
                [
                    {
                        "player_name": "A",
                        "game_date": "2026-04-10",
                        "stat_type": "HITS",
                        "feature_values": {"a": 1.0},
                    }
                ],
                "mlb_v1",
            )
            == 0
        )

    def test_read_player_features_error_returns_none(self):
        assert (
            self._failing_store().read_player_features("p", "2026-04-10", "HITS", "mlb_v1") is None
        )

    def test_read_features_error_returns_empty(self):
        assert self._failing_store().read_features("mlb_v1") == []

    def test_get_feature_set_error_returns_none(self):
        assert self._failing_store().get_feature_set("mlb_v1") is None

    def test_get_feature_set_missing_returns_none(self):
        cur = FakeCursor(fetchone_result=None)
        store = FeatureStore(conn=FakeConn(cur))
        assert store.get_feature_set("nope") is None

    def test_list_feature_sets_error_returns_empty(self):
        assert self._failing_store().list_feature_sets() == []

    def test_list_feature_sets_all_includes_inactive(self):
        cur = FakeCursor(fetchall_result=[("v1", "1.0", 70, False)])
        store = FeatureStore(conn=FakeConn(cur))
        store.list_feature_sets(active_only=False)
        query = cur.executed[-1][0]
        assert "WHERE is_active = TRUE" not in query

    def test_get_coverage_error_returns_zero(self):
        cov = self._failing_store().get_coverage("mlb_v1")
        assert cov == {"count": 0, "min_date": None, "max_date": None}

    def test_get_coverage_null_dates(self):
        cur = FakeCursor(fetchone_result=(0, None, None))
        store = FeatureStore(conn=FakeConn(cur))
        cov = store.get_coverage("mlb_v1")
        assert cov["count"] == 0
        assert cov["min_date"] is None

    def test_update_feature_metadata_error_returns_false(self):
        assert (
            self._failing_store().update_feature_metadata("mlb_v1", "park_factor", train_mean=1.0)
            is False
        )


class TestJsonDefault:
    def test_numpy_and_date(self):
        import numpy as np

        assert _json_default(np.int64(5)) == 5
        assert _json_default(np.float64(1.5)) == 1.5
        assert _json_default(np.array([1, 2])) == [1, 2]
        assert _json_default(date(2026, 4, 10)) == "2026-04-10"
        assert _json_default(np.bool_(True)) is True

    def test_unserializable_raises(self):
        with pytest.raises(TypeError):
            _json_default(object())
