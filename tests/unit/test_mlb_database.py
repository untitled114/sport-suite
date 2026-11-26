"""Tests for mlb.config.database - schema resolution + connection config.

All DB access is mocked; no real connection is ever opened.
"""

import importlib
import warnings
from unittest.mock import MagicMock, patch

import pytest

import mlb.config.database as db


class TestResolve:
    def test_prefixed_name(self):
        assert db._resolve("mlb_players") == "mlb_players"

    def test_bare_alias(self):
        assert db._resolve("players") == "mlb_players"
        assert db._resolve("predictions") == "mlb_predictions"

    def test_unknown_raises(self):
        with pytest.raises(ValueError):
            db._resolve("nonexistent")


class TestSchemaConfig:
    def test_config_fields(self):
        cfg = db.get_schema_config("mlb_intelligence")
        assert cfg["port"] == 5500
        assert cfg["database"] == "sportsuite"
        assert "search_path=mlb_intelligence,public" in cfg["options"]

    def test_bare_name_resolves_search_path(self):
        cfg = db.get_schema_config("teams")
        assert "search_path=mlb_teams,public" in cfg["options"]

    def test_env_overrides(self, monkeypatch):
        monkeypatch.setenv("DB_HOST", "db.example.com")
        monkeypatch.setenv("DB_PORT", "6000")
        monkeypatch.setenv("DB_NAME", "otherdb")
        monkeypatch.setenv("MLB_DB_CONNECT_TIMEOUT", "30")
        cfg = db.get_schema_config("mlb_games")
        assert cfg["host"] == "db.example.com"
        assert cfg["port"] == 6000
        assert cfg["database"] == "otherdb"
        assert cfg["connect_timeout"] == 30


class TestGetConnection:
    def test_default_autocommit_true(self):
        fake_conn = MagicMock()
        with patch("psycopg2.connect", return_value=fake_conn) as mock_connect:
            conn = db.get_connection("mlb_players")
        assert conn is fake_conn
        assert fake_conn.autocommit is True
        # search_path passed through config.
        passed = mock_connect.call_args[1]
        assert "search_path=mlb_players,public" in passed["options"]

    def test_autocommit_false(self):
        fake_conn = MagicMock()
        with patch("psycopg2.connect", return_value=fake_conn):
            db.get_connection("mlb_predictions", autocommit=False)
        assert fake_conn.autocommit is False


class TestConvenienceConfigs:
    def test_all_helpers_route_correctly(self):
        assert "mlb_players" in db.get_players_db_config()["options"]
        assert "mlb_games" in db.get_games_db_config()["options"]
        assert "mlb_teams" in db.get_team_db_config()["options"]
        assert "mlb_intelligence" in db.get_intelligence_db_config()["options"]
        assert "mlb_predictions" in db.get_predictions_db_config()["options"]
        assert "mlb_features" in db.get_features_db_config()["options"]


def test_missing_password_warns(monkeypatch):
    """Re-import the module with no password env to hit the warning branch."""
    monkeypatch.delenv("DB_PASSWORD", raising=False)
    monkeypatch.delenv("MLB_DB_PASSWORD", raising=False)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        importlib.reload(db)
    assert any(issubclass(w.category, RuntimeWarning) for w in caught)
    # Restore module to a clean state for other tests.
    importlib.reload(db)
