"""
Coverage for ``mlb.scripts.loaders.backfill_handedness.HandednessBackfiller``.
=============================================================================
The backfiller updates ``pitcher_profile.throws`` / ``batter_profile.bats`` from
the MLB Stats API ``/people`` endpoint. DB and API are fully mocked: no network
or DB is touched, and the mocks return the minimal shapes the real calls would.
"""

from unittest.mock import MagicMock, patch

import pytest

import mlb.scripts.loaders.backfill_handedness as bh
from mlb.scripts.loaders.backfill_handedness import HandednessBackfiller


@pytest.fixture
def backfiller():
    api = MagicMock()
    return HandednessBackfiller(batch_size=2, api=api)


def _cursor(conn):
    cur = MagicMock()
    cur.__enter__ = lambda s: cur
    cur.__exit__ = lambda s, *a: False
    conn.cursor.return_value = cur
    return cur


class TestConnectClose:
    def test_connect(self, backfiller):
        with (
            patch.object(bh, "psycopg2") as pg,
            patch.object(bh, "get_players_db_config", return_value={"x": 1}),
        ):
            pg.connect.return_value = "CONN"
            backfiller.connect()
        assert backfiller.conn == "CONN"

    def test_close(self, backfiller):
        backfiller.conn = MagicMock()
        backfiller.close()
        backfiller.conn.close.assert_called_once()

    def test_close_noop_when_no_conn(self, backfiller):
        backfiller.conn = None
        assert backfiller.close() is None

    def test_batch_size_floor(self):
        b = HandednessBackfiller(batch_size=0, api=MagicMock())
        assert b.batch_size == 1


class TestFetchIds:
    def test_fetch_ids(self, backfiller):
        conn = MagicMock()
        cur = _cursor(conn)
        cur.fetchall.return_value = [(1,), (2,), (3,)]
        backfiller.conn = conn
        assert backfiller._fetch_ids("pitcher_profile") == [1, 2, 3]


class TestFetchHands:
    def test_batches_and_extracts(self, backfiller):
        # 3 ids, batch_size 2 -> two /people calls
        backfiller.api.get_people.side_effect = [
            [
                {"id": 1, "pitchHand": {"code": "l"}},
                {"id": 2, "pitchHand": {"code": "R"}},
            ],
            [
                {"id": 3, "pitchHand": {"code": "S"}},
                {"id": 99, "pitchHand": None},  # no code -> skipped
                {"id": None, "pitchHand": {"code": "R"}},  # no id -> skipped
            ],
        ]
        hands = backfiller._fetch_hands([1, 2, 3], "pitchHand")
        assert hands == {1: "L", 2: "R", 3: "S"}
        assert backfiller.api.get_people.call_count == 2


class TestBackfillRole:
    def test_no_ids_returns_zero(self, backfiller):
        backfiller._fetch_ids = MagicMock(return_value=[])
        assert backfiller.backfill_role("pitcher_profile", "throws", "pitchHand") == 0

    def test_no_hands_returns_zero(self, backfiller):
        backfiller._fetch_ids = MagicMock(return_value=[1, 2])
        backfiller._fetch_hands = MagicMock(return_value={})
        conn = MagicMock()
        _cursor(conn)
        backfiller.conn = conn
        assert backfiller.backfill_role("pitcher_profile", "throws", "pitchHand") == 0

    def test_updates_rows(self, backfiller):
        backfiller._fetch_ids = MagicMock(return_value=[1, 2])
        backfiller._fetch_hands = MagicMock(return_value={1: "R", 2: "L"})
        conn = MagicMock()
        cur = _cursor(conn)
        cur.rowcount = 2
        backfiller.conn = conn
        with patch.object(bh, "execute_values") as ev:
            n = backfiller.backfill_role("pitcher_profile", "throws", "pitchHand")
        assert n == 2
        conn.commit.assert_called_once()
        # rows passed to execute_values are (player_id, hand) tuples
        rows = ev.call_args.args[2]
        assert sorted(rows) == [(1, "R"), (2, "L")]


class TestRun:
    def test_run_both_roles(self, backfiller):
        backfiller.backfill_role = MagicMock(side_effect=[11, 22])
        out = backfiller.run()
        assert out == {"pitcher_profile": 11, "batter_profile": 22}
        assert backfiller.backfill_role.call_count == 2


class TestMain:
    def test_main_runs_and_closes(self):
        argv = ["prog", "--batch-size", "50"]
        inst = MagicMock()
        inst.run.return_value = {"pitcher_profile": 5, "batter_profile": 7}
        with (
            patch("sys.argv", argv),
            patch.object(bh, "HandednessBackfiller", return_value=inst) as ctor,
        ):
            bh.main()
        ctor.assert_called_once_with(batch_size=50)
        inst.connect.assert_called_once()
        inst.run.assert_called_once()
        inst.close.assert_called_once()

    def test_main_closes_on_error(self):
        argv = ["prog"]
        inst = MagicMock()
        inst.run.side_effect = RuntimeError("boom")
        with (
            patch("sys.argv", argv),
            patch.object(bh, "HandednessBackfiller", return_value=inst),
        ):
            with pytest.raises(RuntimeError):
                bh.main()
        inst.close.assert_called_once()
