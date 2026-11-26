"""Tests for mlb.models.projection_model - park-adjusted projection plumbing.

All database access is mocked. The rows below drive the projection arithmetic and each
branch that builds a query.
"""

from unittest.mock import MagicMock, patch

import pytest

from mlb.models.projection_model import (
    NEUTRAL_PARK_FACTOR,
    PITCHER_STATS,
    STAT_COLUMN_MAP,
    ProjectionModel,
)
from mlb.sql import named_query


def _cursor_conn(rolling_row=None, opp_row=None):
    """Build a conn whose cursor returns rolling_row then opp_row in sequence."""
    conn = MagicMock()
    conn.closed = False
    cur = MagicMock()
    conn.cursor.return_value.__enter__ = MagicMock(return_value=cur)
    conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
    rows = [r for r in (rolling_row, opp_row) if r is not None]
    cur.fetchone.side_effect = rows if rows else [None]
    return conn, cur


class TestConnectionHelpers:
    def test_lazy_players_conn(self):
        model = ProjectionModel()
        with patch("mlb.models.projection_model.get_connection") as gc:
            gc.return_value = MagicMock(closed=False)
            model._get_players_conn()
            gc.assert_called_once_with("mlb_players", autocommit=True)

    def test_lazy_teams_conn(self):
        model = ProjectionModel()
        with patch("mlb.models.projection_model.get_connection") as gc:
            gc.return_value = MagicMock(closed=False)
            model._get_teams_conn()
            gc.assert_called_once_with("mlb_teams", autocommit=True)

    def test_reuses_open_conn(self):
        model = ProjectionModel()
        existing = MagicMock(closed=False)
        model._players_conn = existing
        assert model._get_players_conn() is existing

    def test_reopens_closed_conn(self):
        model = ProjectionModel()
        model._players_conn = MagicMock(closed=True)
        with patch("mlb.models.projection_model.get_connection") as gc:
            gc.return_value = MagicMock(closed=False)
            model._get_players_conn()
            gc.assert_called_once()


class TestResolveParkFactor:
    def test_none_returns_neutral(self):
        model = ProjectionModel()
        assert model._resolve_park_factor(None, "HITS") == NEUTRAL_PARK_FACTOR

    def test_clamps_high_and_low(self):
        model = ProjectionModel()
        assert model._resolve_park_factor(2.0, "HITS") == 1.15
        assert model._resolve_park_factor(0.1, "HITS") == 0.85

    def test_strikeouts_damped(self):
        model = ProjectionModel()
        # pf clamped to 1.15, then damped 50% toward 1.0 -> 1.075
        assert model._resolve_park_factor(1.15, "STRIKEOUTS") == pytest.approx(1.075)


class TestRollingStats:
    def test_no_row_returns_defaults(self):
        model = ProjectionModel()
        conn, _ = _cursor_conn(rolling_row=None)
        model._players_conn = conn
        out = model._get_player_rolling_stats("Nobody", "hits", is_pitcher=False)
        assert out == {"L5": 0.0, "L10": 0.0, "L20": 0.0, "std": 1.5, "team": ""}

    def test_row_none_first_value_returns_defaults(self):
        model = ProjectionModel()
        conn, _ = _cursor_conn(rolling_row=(None, None, None, None, None))
        model._players_conn = conn
        out = model._get_player_rolling_stats("X", "hits", is_pitcher=False)
        assert out["L5"] == 0.0 and out["std"] == 1.5

    def test_full_row_mapped(self):
        model = ProjectionModel()
        conn, _ = _cursor_conn(rolling_row=(2.0, 1.8, 1.6, 0.5, "BOS"))
        model._players_conn = conn
        out = model._get_player_rolling_stats("X", "hits", is_pitcher=False)
        assert out["L5"] == 2.0 and out["L10"] == 1.8 and out["team"] == "BOS"
        assert out["std"] == 0.5

    def test_fallback_chain_when_l10_l20_null(self):
        model = ProjectionModel()
        # L10/L20 null -> fall back to L5; std null -> default 1.5
        conn, _ = _cursor_conn(rolling_row=(3.0, None, None, None, None))
        model._players_conn = conn
        out = model._get_player_rolling_stats("X", "strikeouts", is_pitcher=True)
        assert out["L10"] == 3.0 and out["L20"] == 3.0
        assert out["std"] == 1.5 and out["team"] == ""

    def test_pitcher_uses_pitcher_table(self):
        model = ProjectionModel()
        conn, cur = _cursor_conn(rolling_row=(5.0, 5.0, 5.0, 1.0, "NYY"))
        model._players_conn = conn
        model._get_player_rolling_stats("Ace", "strikeouts", is_pitcher=True)
        sql = cur.execute.call_args[0][0]
        assert "pitcher_game_logs" in sql

    def test_batter_uses_batter_table(self):
        model = ProjectionModel()
        conn, cur = _cursor_conn(rolling_row=(1.0, 1.0, 1.0, 0.5, "LAD"))
        model._players_conn = conn
        model._get_player_rolling_stats("Bat", "hits", is_pitcher=False)
        sql = cur.execute.call_args[0][0]
        assert "batter_game_logs" in sql


class TestOpponentFactor:
    def test_empty_opponent_returns_one(self):
        model = ProjectionModel()
        assert model._get_opponent_factor("", "HITS", is_pitcher=False) == 1.0

    def test_pitcher_query_branch(self):
        model = ProjectionModel()
        conn, cur = _cursor_conn(opp_row=(0.25, 0.22))
        model._players_conn = conn  # the lineup's rate comes from the batter game logs
        f = model._get_opponent_factor("BOS", "STRIKEOUTS", is_pitcher=True)
        sql = cur.execute.call_args[0][0]
        assert sql == named_query("opponent_strikeout_rate_vs_league")
        assert cur.execute.call_args[0][1] == ("BOS",)
        assert f == pytest.approx(max(0.90, min(1.10, 0.25 / 0.22)))

    def test_batter_query_branch(self):
        model = ProjectionModel()
        conn, cur = _cursor_conn(opp_row=(4.5, 4.0))
        model._teams_conn = conn
        f = model._get_opponent_factor("NYY", "HITS", is_pitcher=False)
        sql = cur.execute.call_args[0][0]
        assert sql == named_query("opponent_runs_allowed_vs_league")
        assert f == pytest.approx(max(0.90, min(1.10, 4.5 / 4.0)))

    def test_no_row_returns_one(self):
        model = ProjectionModel()
        conn, _ = _cursor_conn(opp_row=None)
        model._teams_conn = conn
        assert model._get_opponent_factor("XXX", "HITS", is_pitcher=False) == 1.0

    def test_null_value_returns_one(self):
        model = ProjectionModel()
        conn, _ = _cursor_conn(opp_row=(None, 4.0))
        model._teams_conn = conn
        assert model._get_opponent_factor("XXX", "HITS", is_pitcher=False) == 1.0

    def test_null_league_avg_returns_one(self):
        model = ProjectionModel()
        conn, _ = _cursor_conn(opp_row=(4.5, None))
        model._teams_conn = conn
        assert model._get_opponent_factor("XXX", "HITS", is_pitcher=False) == 1.0

    def test_zero_league_avg_returns_one(self):
        model = ProjectionModel()
        conn, _ = _cursor_conn(opp_row=(4.5, 0.0))
        model._teams_conn = conn
        assert model._get_opponent_factor("XXX", "HITS", is_pitcher=False) == 1.0

    def test_clamps_factor(self):
        model = ProjectionModel()
        conn, _ = _cursor_conn(opp_row=(10.0, 1.0))  # ratio 10 -> clamp to 1.10
        model._teams_conn = conn
        assert model._get_opponent_factor("XXX", "HITS", is_pitcher=False) == 1.10

    def test_exception_returns_one(self):
        model = ProjectionModel()
        conn = MagicMock(closed=False)
        conn.cursor.side_effect = RuntimeError("db down")
        model._teams_conn = conn
        assert model._get_opponent_factor("XXX", "HITS", is_pitcher=False) == 1.0


class TestProjectWithDetails:
    def _model(self, rolling, opp_row=(4.5, 4.0)):
        model = ProjectionModel()
        # a pitcher's opponent factor is read on the players connection, after the rolling row
        pconn, _ = _cursor_conn(rolling_row=rolling, opp_row=opp_row)
        tconn, _ = _cursor_conn(opp_row=opp_row)
        model._players_conn = pconn
        model._teams_conn = tconn
        return model

    def test_batter_home(self):
        model = self._model((2.0, 1.8, 1.6, 0.5, "BOS"))
        details = model.project_with_details("Bat", "HITS", "NYY", is_home=True, park_factor=1.05)
        assert details["stat_type"] == "HITS"
        assert details["is_pitcher"] is False
        assert details["is_home"] is True
        assert details["home_factor"] > 1.0
        assert details["projection"] >= 0

    def test_pitcher_away(self):
        model = self._model((6.0, 6.0, 6.0, 1.5, "NYY"), opp_row=(0.25, 0.22))
        details = model.project_with_details(
            "Ace", "STRIKEOUTS", "BOS", is_home=False, park_factor=1.10
        )
        assert details["is_pitcher"] is True
        assert details["home_factor"] < 1.0

    def test_an_unknown_stat_type_is_an_error_not_sql(self):
        model = self._model((1.0, 1.0, 1.0, 0.3, "LAD"))
        with pytest.raises(ValueError, match="unknown stat type 'DOUBLES'"):
            model.project_with_details("X", "DOUBLES", "NYY")
        with pytest.raises(ValueError, match="no rolling stats for column"):
            model._get_player_rolling_stats("X", "hits; DROP TABLE x", is_pitcher=False)

    def test_as_of_bounds_the_rolling_window(self):
        model = ProjectionModel()
        conn, cur = _cursor_conn(rolling_row=(2.0, 1.8, 1.6, 0.5, "BOS"))
        model._players_conn = conn
        model._get_player_rolling_stats("Bat", "total_bases", False, as_of="2026-09-01")
        sql, params = cur.execute.call_args[0]
        assert "FROM batter_game_logs" in sql and "total_bases AS val" in sql
        assert "{" not in sql and params == ("Bat", "2026-09-01", "2026-09-01")

    def test_project_returns_scalar(self):
        model = self._model((2.0, 2.0, 2.0, 0.5, "BOS"))
        val = model.project("Bat", "TOTAL_BASES", "NYY", is_home=True)
        assert isinstance(val, float)

    def test_home_advantage_per_stat_type(self):
        model = self._model((1.0, 1.0, 1.0, 0.3, "LAD"))
        d_home = model.project_with_details("X", "HITS", "NYY", is_home=True)
        assert d_home["home_factor"] == 1.01


class TestStdDev:
    def test_returns_std(self):
        model = ProjectionModel()
        conn, _ = _cursor_conn(rolling_row=(2.0, 2.0, 2.0, 0.8, "BOS"))
        model._players_conn = conn
        assert model.get_player_std_dev("Bat", "HITS") == 0.8

    def test_default_when_missing(self):
        model = ProjectionModel()
        conn, _ = _cursor_conn(rolling_row=None)
        model._players_conn = conn
        assert model.get_player_std_dev("Nobody", "HITS") == 1.5


class TestClose:
    def test_close_open_conns(self):
        model = ProjectionModel()
        p = MagicMock(closed=False)
        t = MagicMock(closed=False)
        model._players_conn = p
        model._teams_conn = t
        model.close()
        p.close.assert_called_once()
        t.close.assert_called_once()

    def test_close_skips_already_closed(self):
        model = ProjectionModel()
        p = MagicMock(closed=True)
        model._players_conn = p
        model._teams_conn = None
        model.close()
        p.close.assert_not_called()

    def test_del_swallows_errors(self):
        model = ProjectionModel()
        model._players_conn = MagicMock(closed=False)
        model._players_conn.close.side_effect = RuntimeError("x")
        assert model.__del__() is None


def test_stat_constants():
    assert "STRIKEOUTS" in STAT_COLUMN_MAP
    assert PITCHER_STATS == ("STRIKEOUTS",)
