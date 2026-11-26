"""
Centralized Database Configuration for NBA Suite
=================================================
All database connection configs should be imported from this module.
Connection settings come from environment variables; the password has no default.

Environment Variables:
    DB_USER: Database username (fallback: 'postgres')
    DB_PASSWORD: Default database password (REQUIRED - no hardcoded default)
    NBA_*_DB_*: Per-database overrides (host, port, user, password, name)

Usage:
    from nba.config.database import get_intelligence_db_config

    config = get_intelligence_db_config()
    conn = psycopg2.connect(**config)
"""

import os
from typing import Any, Dict

# Default credentials from environment (REQUIRED - no hardcoded passwords)
DB_DEFAULT_USER = os.getenv("NBA_DB_USER", os.getenv("DB_USER", "postgres"))
DB_DEFAULT_PASSWORD = os.getenv("NBA_DB_PASSWORD", os.getenv("DB_PASSWORD"))

if DB_DEFAULT_PASSWORD is None:
    import warnings

    warnings.warn(
        "DB_PASSWORD environment variable not set. "
        "Database connections will fail until credentials are configured.",
        RuntimeWarning,
        stacklevel=2,
    )


def get_db_config(
    env_prefix: str, default_port: int, default_database: str, default_host: str = "localhost"
) -> Dict[str, Any]:
    """
    Build database config dict from environment variables.

    Args:
        env_prefix: Prefix for env vars (e.g., 'NBA_INT' -> NBA_INT_DB_HOST)
        default_port: Default port if not in env
        default_database: Default database name if not in env
        default_host: Default host if not in env

    Returns:
        Dict with psycopg2 connection parameters
    """
    return {
        "host": os.getenv(f"{env_prefix}_DB_HOST", default_host),
        "port": int(os.getenv(f"{env_prefix}_DB_PORT", default_port)),
        "database": os.getenv(f"{env_prefix}_DB_NAME", default_database),
        "user": os.getenv(f"{env_prefix}_DB_USER", DB_DEFAULT_USER),
        "password": os.getenv(f"{env_prefix}_DB_PASSWORD", DB_DEFAULT_PASSWORD),
        "connect_timeout": int(os.getenv("NBA_DB_CONNECT_TIMEOUT", 10)),
    }


def get_players_db_config() -> Dict[str, Any]:
    """Get players database config."""
    return get_schema_config("players")


def get_games_db_config() -> Dict[str, Any]:
    """Get games database config."""
    return get_schema_config("games")


def get_team_db_config() -> Dict[str, Any]:
    """Get teams database config."""
    return get_schema_config("teams")


def get_intelligence_db_config() -> Dict[str, Any]:
    """Get intelligence database config."""
    return get_schema_config("intelligence")


# ==========================================================================
# Connections to the schemas
# Single TimescaleDB instance with schema-based isolation (port 5500).
# DB_PORT and DB_NAME select the instance and the database.
# ==========================================================================

SCHEMAS = {
    "players": "players",
    "games": "games",
    "teams": "teams",
    "intelligence": "intelligence",
    "predictions": "predictions",
    "features": "features",
}


def get_schema_config(schema: str) -> Dict[str, Any]:
    """
    Get connection config for the database with search_path
    set to the given schema. Existing queries work without schema-qualifying.
    """
    if schema not in SCHEMAS:
        raise ValueError(f"Unknown schema: {schema}. Valid: {list(SCHEMAS.keys())}")

    return {
        "host": os.getenv("DB_HOST", "localhost"),
        "port": int(os.getenv("DB_PORT", "5500")),
        "database": os.getenv("DB_NAME", "sportsuite"),
        "user": os.getenv("DB_USER", DB_DEFAULT_USER),
        "password": os.getenv("DB_PASSWORD", DB_DEFAULT_PASSWORD),
        "connect_timeout": int(os.getenv("NBA_DB_CONNECT_TIMEOUT", 10)),
        "options": f"-c search_path={SCHEMAS[schema]},public",
    }


def get_connection(schema: str, autocommit: bool = True):
    """
    Get a psycopg2 connection to the database with the given schema.

    Args:
        schema: One of players, games, teams, intelligence, predictions, features
        autocommit: Set autocommit (default True to prevent silent failures)
    """
    import psycopg2

    config = get_schema_config(schema)
    conn = psycopg2.connect(**config)
    conn.autocommit = autocommit
    return conn


def get_predictions_db_config() -> Dict[str, Any]:
    """Get predictions schema config for the database."""
    return get_schema_config("predictions")


def get_features_db_config() -> Dict[str, Any]:
    """Get features schema config for the database."""
    return get_schema_config("features")
