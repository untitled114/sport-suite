"""
MLB Player Data Loader
======================
Loads active 26-man rosters for all 30 teams into the two MLB player profile
tables, split by role:
    - Pitchers (position type "Pitcher") -> mlb_players.pitcher_profile
    - Everyone else (batters)            -> mlb_players.batter_profile

Mirrors ``nba/scripts/loaders/load_nba_players.py`` but adapts to baseball's
two-player-type domain model.

Usage:
    # Load active rosters for all teams
    python -m mlb.scripts.loaders.load_mlb_players

    # Load a single team's roster
    python -m mlb.scripts.loaders.load_mlb_players --team-id 147
"""

import argparse
import logging
import sys
from pathlib import Path
from typing import List, Optional

import psycopg2
from psycopg2.extras import execute_values

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from mlb.config.database import get_players_db_config
from mlb.scripts.utilities.statsapi_wrapper import StatsAPIWrapper

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

DB_CONFIG = get_players_db_config()


class MLBPlayerLoader:
    """Loads MLB pitcher/batter profiles from active rosters."""

    def __init__(self):
        self.api = StatsAPIWrapper()
        self.conn = None
        self.team_map = {}

    def connect(self):
        """Establish database connection."""
        try:
            self.conn = psycopg2.connect(**DB_CONFIG)
            logger.info("Connected to mlb_players database")
        except (psycopg2.Error, KeyError, TypeError, ValueError) as e:
            logger.error(f"Database connection failed: {e}")
            raise

    def close(self):
        """Close database connection."""
        if self.conn:
            self.conn.close()
            logger.info("Database connection closed")

    def _team_ids(self, team_id: Optional[int]) -> List[int]:
        """Resolve the set of team ids to load rosters for."""
        if not self.team_map:
            self.team_map = self.api.get_team_id_map()
        if team_id is not None:
            return [team_id]
        return list(self.team_map.keys())

    def load_rosters(self, team_id: Optional[int] = None) -> dict:
        """
        Load active rosters into pitcher_profile / batter_profile.

        Args:
            team_id: Optional single team id; loads all 30 if omitted.

        Returns:
            Dict with counts: {"pitchers": int, "batters": int}.
        """
        pitcher_rows = []
        batter_rows = []

        for tid in self._team_ids(team_id):
            abbrev = self.team_map.get(tid)
            logger.info(f"Fetching roster for {abbrev or tid}...")

            try:
                roster = self.api.get_roster(tid, roster_type="active")
            except (psycopg2.Error, KeyError, TypeError, ValueError) as e:
                logger.warning(f"Failed to fetch roster for {abbrev or tid}: {e}")
                continue

            for entry in roster:
                person = entry.get("person", {})
                position = entry.get("position", {})
                player_id = person.get("id")
                full_name = person.get("fullName")
                if player_id is None or not full_name:
                    continue

                pos_abbrev = position.get("abbreviation")
                pos_type = position.get("type")
                row = (player_id, full_name, abbrev, pos_abbrev)

                # Two-way players (e.g. position type "Two-Way Player") land in both.
                if pos_type == "Pitcher":
                    pitcher_rows.append(row)
                else:
                    batter_rows.append(row)
                    if pos_type == "Two-Way Player":
                        pitcher_rows.append(row)

        cursor = self.conn.cursor()

        if pitcher_rows:
            execute_values(
                cursor,
                """
                INSERT INTO pitcher_profile (player_id, full_name, team, position)
                VALUES %s
                ON CONFLICT (player_id)
                DO UPDATE SET
                    full_name = EXCLUDED.full_name,
                    team = EXCLUDED.team,
                    position = EXCLUDED.position
                """,
                pitcher_rows,
            )
            logger.info(f"Loaded {len(pitcher_rows)} pitcher profiles")

        if batter_rows:
            execute_values(
                cursor,
                """
                INSERT INTO batter_profile (player_id, full_name, team, position)
                VALUES %s
                ON CONFLICT (player_id)
                DO UPDATE SET
                    full_name = EXCLUDED.full_name,
                    team = EXCLUDED.team,
                    position = EXCLUDED.position
                """,
                batter_rows,
            )
            logger.info(f"Loaded {len(batter_rows)} batter profiles")

        self.conn.commit()
        return {"pitchers": len(pitcher_rows), "batters": len(batter_rows)}


def main():
    parser = argparse.ArgumentParser(description="Load MLB active rosters into database")
    parser.add_argument(
        "--team-id", type=int, default=None, help="Single team id to load (default: all 30)"
    )
    args = parser.parse_args()

    loader = MLBPlayerLoader()
    try:
        loader.connect()
        counts = loader.load_rosters(team_id=args.team_id)
        logger.info(
            f" Player loading complete! "
            f"{counts['pitchers']} pitchers, {counts['batters']} batters"
        )
    except (psycopg2.Error, KeyError, TypeError, ValueError) as e:
        logger.error(f"Loading failed: {e}")
        raise
    finally:
        loader.close()


if __name__ == "__main__":
    main()
