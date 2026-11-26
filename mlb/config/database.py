"""
Centralized Database Configuration for MLB Suite
=================================================
Routes to the same TimescaleDB instance as NBA (port 5500,
database ``sportsuite``) but isolates all MLB data under ``mlb_*`` schemas.

This mirrors ``nba/config/database.py``. Credentials are read from the same
environment variables (``DB_USER``, ``DB_PASSWORD``, ``DB_HOST``, ``DB_PORT``,
``DB_NAME``) so a single ``.env`` serves both sports.

Usage:
    from mlb.config.database import get_connection
    conn = get_connection("mlb_players")  # autocommit=True by default
"""

import os
from typing import Any, Dict

# Default credentials from environment (REQUIRED - no hardcoded passwords).
DB_DEFAULT_USER = os.getenv("MLB_DB_USER", os.getenv("DB_USER", "postgres"))
DB_DEFAULT_PASSWORD = os.getenv("MLB_DB_PASSWORD", os.getenv("DB_PASSWORD"))

if DB_DEFAULT_PASSWORD is None:
    import warnings

    warnings.warn(
        "DB_PASSWORD environment variable not set. "
        "Database connections will fail until credentials are configured.",
        RuntimeWarning,
        stacklevel=2,
    )

# ==========================================================================
# Connections to the MLB schemas
# Single TimescaleDB instance, schema-based isolation (port 5500).
# Both bare ("intelligence") and prefixed ("mlb_intelligence") names accepted.
# ==========================================================================

SCHEMAS = {
    "mlb_players": "mlb_players",
    "mlb_games": "mlb_games",
    "mlb_teams": "mlb_teams",
    "mlb_intelligence": "mlb_intelligence",
    "mlb_predictions": "mlb_predictions",
    "mlb_features": "mlb_features",
}

# Convenience aliases so callers can pass the bare schema name.
_ALIASES = {k.replace("mlb_", ""): k for k in SCHEMAS}


def _resolve(schema: str) -> str:
    """Resolve a bare ('players') or prefixed ('mlb_players') schema name."""
    if schema in SCHEMAS:
        return SCHEMAS[schema]
    if schema in _ALIASES:
        return SCHEMAS[_ALIASES[schema]]
    raise ValueError(f"Unknown schema: {schema}. Valid: {list(SCHEMAS.keys())}")


def get_schema_config(schema: str) -> Dict[str, Any]:
    """
    Get connection config for the database with search_path
    set to the given MLB schema. Existing queries work without schema-qualifying.
    """
    resolved = _resolve(schema)
    return {
        "host": os.getenv("DB_HOST", "localhost"),
        "port": int(os.getenv("DB_PORT", "5500")),
        "database": os.getenv("DB_NAME", "sportsuite"),
        "user": os.getenv("DB_USER", DB_DEFAULT_USER),
        "password": os.getenv("DB_PASSWORD", DB_DEFAULT_PASSWORD),
        "connect_timeout": int(os.getenv("MLB_DB_CONNECT_TIMEOUT", 10)),
        "options": f"-c search_path={resolved},public",
    }


def get_connection(schema: str, autocommit: bool = True):
    """
    Get a psycopg2 connection to the database with the given MLB schema.

    Args:
        schema: One of mlb_players, mlb_games, mlb_teams, mlb_intelligence,
                mlb_predictions, mlb_features (bare names also accepted).
        autocommit: Set autocommit (default True to prevent silent failures).
    """
    import psycopg2

    config = get_schema_config(schema)
    conn = psycopg2.connect(**config)
    conn.autocommit = autocommit
    return conn


def get_players_db_config() -> Dict[str, Any]:
    """Get mlb_players schema config."""
    return get_schema_config("mlb_players")


def get_games_db_config() -> Dict[str, Any]:
    """Get mlb_games schema config."""
    return get_schema_config("mlb_games")


def get_team_db_config() -> Dict[str, Any]:
    """Get mlb_teams schema config."""
    return get_schema_config("mlb_teams")


def get_intelligence_db_config() -> Dict[str, Any]:
    """Get mlb_intelligence schema config."""
    return get_schema_config("mlb_intelligence")


def get_predictions_db_config() -> Dict[str, Any]:
    """Get mlb_predictions schema config."""
    return get_schema_config("mlb_predictions")


def get_features_db_config() -> Dict[str, Any]:
    """Get mlb_features schema config."""
    return get_schema_config("mlb_features")
