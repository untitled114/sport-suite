"""
MLB Team Data Loader
====================
Loads all 30 MLB teams into ``mlb_teams.teams`` via the MLB Stats API.
Mirrors ``nba/scripts/loaders/load_nba_teams.py``.

Usage:
    python -m mlb.scripts.loaders.load_mlb_teams
"""

import argparse
import logging
import sys
from pathlib import Path

import psycopg2
from psycopg2.extras import execute_values

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from mlb.config.database import get_team_db_config
from mlb.scripts.utilities.statsapi_wrapper import StatsAPIWrapper

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

DB_CONFIG = get_team_db_config()


class MLBTeamLoader:
    """Loads MLB team reference data into ``mlb_teams.teams``."""

    def __init__(self):
        self.api = StatsAPIWrapper()
        self.conn = None

    def connect(self):
        """Establish database connection."""
        try:
            self.conn = psycopg2.connect(**DB_CONFIG)
            logger.info("Connected to mlb_teams database")
        except (psycopg2.Error, KeyError, TypeError, ValueError) as e:
            logger.error(f"Database connection failed: {e}")
            raise

    def close(self):
        """Close database connection."""
        if self.conn:
            self.conn.close()
            logger.info("Database connection closed")

    def load_teams(self) -> int:
        """
        Load all MLB teams.

        Returns:
            Number of teams loaded.
        """
        logger.info("Loading MLB teams...")

        teams = self.api.get_teams()
        if not teams:
            logger.warning("No teams found")
            return 0

        insert_data = []
        for t in teams:
            insert_data.append(
                (
                    t["id"],
                    t.get("abbreviation"),
                    t.get("name"),
                    t.get("league", {}).get("name"),
                    t.get("division", {}).get("name"),
                    t.get("venue", {}).get("name"),
                )
            )

        cursor = self.conn.cursor()
        insert_query = """
            INSERT INTO teams (team_id, team_abbrev, team_name, league, division, venue)
            VALUES %s
            ON CONFLICT (team_id)
            DO UPDATE SET
                team_abbrev = EXCLUDED.team_abbrev,
                team_name = EXCLUDED.team_name,
                league = EXCLUDED.league,
                division = EXCLUDED.division,
                venue = EXCLUDED.venue
        """
        execute_values(cursor, insert_query, insert_data)
        self.conn.commit()

        logger.info(f"Loaded {len(insert_data)} teams")
        return len(insert_data)


def main():
    parser = argparse.ArgumentParser(description="Load MLB team data into database")
    parser.parse_args()

    loader = MLBTeamLoader()
    try:
        loader.connect()
        loader.load_teams()
        logger.info("Team data loading complete!")
    except (psycopg2.Error, KeyError, TypeError, ValueError) as e:
        logger.error(f"Loading failed: {e}")
        raise
    finally:
        loader.close()


if __name__ == "__main__":
    main()
