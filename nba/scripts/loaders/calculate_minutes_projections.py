"""
Calculate Minutes Projections for NBA Players
Projects minutes per game for current season based on historical data, age, and role changes.

Methodology:
1. Start with previous season's MPG as baseline
2. Apply age-based decline factor (for players 33+)
3. Apply experience-based adjustments (rookies, 2nd year players)
4. Flag high-uncertainty cases (role changes, new team, injury history)
5. Update with actual data after 5/10 games

Usage:
    # Calculate projections
    python calculate_minutes_projections.py

    # Update database
    python calculate_minutes_projections.py --update

    # Recalculate after N games
    python calculate_minutes_projections.py --update --min-games 5
"""

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd
import psycopg2
from psycopg2.extras import execute_values

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from nba.config.database import get_players_db_config
from nba.sql import named_query
from nba.utils.season import current_season, previous_nba_season

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# Database connection params
DB_CONFIG = get_players_db_config()


class MinutesProjector:
    """Projects minutes per game for NBA players"""

    def __init__(self):
        self.conn = None
        self.player_data = None
        self.projections = []

    def connect(self):
        """Establish database connection"""
        try:
            self.conn = psycopg2.connect(**DB_CONFIG)
            logger.info("Connected to the players schema")
        except (psycopg2.Error, KeyError, TypeError, ValueError) as e:
            logger.error(f"Database connection failed: {e}")
            raise

    def close(self):
        """Close database connection"""
        if self.conn:
            self.conn.close()
            logger.info("Database connection closed")

    def load_player_data(self, min_games: int = 0) -> pd.DataFrame:
        """
        Load player historical data

        Args:
            min_games: Minimum games in current season to use actual data

        Returns:
            DataFrame with player stats
        """
        logger.info(f"\nLoading player data...")

        query = named_query("players_with_two_seasons_of_minutes")

        curr = current_season()  # seasons are stored under their end year
        df = pd.read_sql(query, self.conn, params={"curr": curr, "prev": curr - 1})

        logger.info(f"Loaded {len(df)} players")
        logger.info(f"   Players with prev season data: {df['prev_mpg'].notna().sum()}")
        logger.info(f"   Players with current season data: {df['curr_mpg'].notna().sum()}")

        self.player_data = df
        return df

    def apply_age_adjustment(self, mpg: float, age: int) -> Tuple[float, str]:
        """
        Apply age-based adjustment to minutes

        Args:
            mpg: Previous season MPG
            age: Player's current age

        Returns:
            (adjusted_mpg, reason)
        """
        if pd.isna(age) or age < 30:
            return mpg, "No adjustment"

        # Age-based decline factors (based on NBA historical data)
        if age >= 38:
            factor = 0.85  # ~15% reduction for very old players
            reason = "Age 38+ decline"
        elif age >= 35:
            factor = 0.92  # ~8% reduction
            reason = "Age 35+ decline"
        elif age >= 33:
            factor = 0.97  # ~3% reduction
            reason = "Age 33+ decline"
        else:
            return mpg, "No adjustment"

        adjusted = mpg * factor
        return adjusted, reason

    def apply_experience_adjustment(
        self, mpg: float, experience: int, prev_gp: int
    ) -> Tuple[float, str]:
        """
        Apply experience-based adjustment

        Args:
            mpg: Previous season MPG
            experience: Years in NBA
            prev_gp: Games played previous season

        Returns:
            (adjusted_mpg, reason)
        """
        if pd.isna(experience):
            return mpg, "Unknown experience"

        # Rookies/second year players often see minutes increase
        if experience == 1:  # Second year player
            if pd.notna(prev_gp) and prev_gp > 40 and mpg > 15:
                # Promising rookie likely gets more minutes
                adjusted = min(mpg * 1.10, 36)  # Cap at 36 MPG
                return adjusted, "2nd year bump"
            else:
                return mpg, "No adjustment"

        # Very experienced players (10+ years) often stable or declining
        if experience >= 10 and mpg > 30:
            # Veterans often get slight load management
            adjusted = mpg * 0.98
            return adjusted, "Veteran load management"

        return mpg, "No adjustment"

    def flag_uncertainty(self, row: pd.Series) -> str:
        """
        Flag cases with high projection uncertainty

        Returns:
            Uncertainty flag/reason
        """
        flags = []

        # Missing previous season data
        if pd.isna(row["prev_mpg"]):
            return "HIGH - No previous season data"

        # Very low games played last season (injury concerns)
        if pd.notna(row["prev_gp"]) and row["prev_gp"] < 20:
            flags.append("Injury history")

        # Extreme age
        if pd.notna(row["estimated_age"]) and row["estimated_age"] >= 38:
            flags.append("Advanced age")

        # Rookie (no baseline)
        if pd.notna(row["years_experience"]) and row["years_experience"] == 0:
            return "HIGH - Rookie (no NBA history)"

        # Low minutes last year (role player uncertainty)
        if row["prev_mpg"] < 10:
            flags.append("Low usage")

        if flags:
            return "MEDIUM - " + ", ".join(flags)

        return "LOW"

    def calculate_projections(self, min_games: int = 5) -> pd.DataFrame:
        """
        Calculate minutes projections for all players

        Args:
            min_games: Use actual current season data if >= this many games

        Returns:
            DataFrame with projections
        """
        logger.info(f"\nCalculating minutes projections...")

        df = self.player_data.copy()

        projections = []

        for _, row in df.iterrows():
            projection = {
                "player_id": row["player_id"],
                "player_name": row["full_name"],
                "team": row["team_abbrev"],
                "position": row["position"],
                "curr_mpg": row["curr_mpg"],
            }

            # If we have current season data (>= min_games), use that
            if pd.notna(row["curr_gp"]) and row["curr_gp"] >= min_games:
                projection["projected_mpg"] = row["curr_mpg"]
                projection["method"] = f'Actual (from {int(row["curr_gp"])} games)'
                projection["uncertainty"] = "LOW"

            # Otherwise, project from previous season
            elif pd.notna(row["prev_mpg"]):
                base_mpg = row["prev_mpg"]

                # Apply age adjustment
                mpg_after_age, age_reason = self.apply_age_adjustment(
                    base_mpg, row["estimated_age"]
                )

                # Apply experience adjustment
                mpg_final, exp_reason = self.apply_experience_adjustment(
                    mpg_after_age, row["years_experience"], row["prev_gp"]
                )

                # Combine reasons
                reasons = []
                if age_reason != "No adjustment":
                    reasons.append(age_reason)
                if exp_reason != "No adjustment":
                    reasons.append(exp_reason)

                method = f"Baseline from {previous_nba_season()}"
                if reasons:
                    method += f" + {', '.join(reasons)}"

                projection["projected_mpg"] = round(mpg_final, 1)
                projection["baseline_mpg"] = round(base_mpg, 1)
                projection["method"] = method
                projection["uncertainty"] = self.flag_uncertainty(row)

            else:
                # No data available
                projection["projected_mpg"] = None
                projection["baseline_mpg"] = None
                projection["method"] = "No data"
                projection["uncertainty"] = "HIGH - No historical data"

            projections.append(projection)

        proj_df = pd.DataFrame(projections)

        logger.info(f"Calculated {len(proj_df)} projections")

        # Summary by uncertainty
        if "uncertainty" in proj_df.columns:
            logger.info(f"\nUNCERTAINTY SUMMARY:")
            for unc_level in ["LOW", "MEDIUM", "HIGH"]:
                count = proj_df["uncertainty"].str.contains(unc_level, na=False).sum()
                logger.info(f"   {unc_level}: {count} players")

        self.projections = proj_df
        return proj_df

    def save_to_database(self):
        """Save projections to database"""
        logger.info(f"\nSaving projections to database...")

        cursor = self.conn.cursor()

        # Insert/update projections
        insert_data = []
        for _, row in self.projections.iterrows():
            if pd.notna(row["projected_mpg"]):
                curr_mpg = row.get("curr_mpg")
                insert_data.append(
                    (
                        int(row["player_id"]),
                        float(row["projected_mpg"]),
                        float(row["baseline_mpg"]) if pd.notna(row["baseline_mpg"]) else None,
                        float(curr_mpg) if curr_mpg is not None and pd.notna(curr_mpg) else None,
                        str(row["method"])[:50],  # method is VARCHAR(50)
                    )
                )

        if insert_data:
            insert_query = named_query("minutes_projections_upsert")

            execute_values(cursor, insert_query, insert_data)
            self.conn.commit()

            logger.info(f"Saved {len(insert_data)} projections to database")
        else:
            logger.warning("No projections to save")

    def display_sample_projections(self, n: int = 20):
        """Display sample projections"""
        logger.info(f"\nSAMPLE PROJECTIONS:")

        # Show a mix: stars, role players, rookies
        if len(self.projections) > 0:
            # Sort by projected MPG
            sorted_proj = self.projections.sort_values(
                "projected_mpg", ascending=False, na_position="last"
            )

            logger.info(f"\n   Top Minutes Players:")
            for _, row in sorted_proj.head(10).iterrows():
                logger.info(
                    f"      {row['player_name']} ({row['team']}): "
                    f"{row['projected_mpg']:.1f} MPG ({row['uncertainty']})"
                )

            logger.info(f"\n   High Uncertainty Players:")
            high_unc = sorted_proj[sorted_proj["uncertainty"].str.contains("HIGH", na=False)].head(
                5
            )
            for _, row in high_unc.iterrows():
                logger.info(
                    f"      {row['player_name']} ({row['team']}): "
                    f"{row['projected_mpg']} MPG - {row['uncertainty']}"
                )

    def run(self, update_db: bool = False, min_games: int = 5):
        """
        Main execution flow

        Args:
            update_db: Save to database
            min_games: Minimum games for using actual data
        """
        try:
            # Load data
            self.load_player_data(min_games=min_games)

            # Calculate projections
            self.calculate_projections(min_games=min_games)

            # Display samples
            self.display_sample_projections()

            # Save to database
            if update_db:
                self.save_to_database()
            else:
                logger.info(f"\nRun with --update to save projections to database")

            logger.info(f"\nMinutes projection complete!")

        except (psycopg2.Error, KeyError, TypeError, ValueError) as e:
            logger.error(f"Projection failed: {e}")
            if self.conn:
                self.conn.rollback()
            raise


def main():
    parser = argparse.ArgumentParser(description="Calculate NBA player minutes projections")
    parser.add_argument("--update", action="store_true", help="Save projections to database")
    parser.add_argument(
        "--min-games",
        type=int,
        default=5,
        help="Minimum current season games to use actual data (default: 5)",
    )

    args = parser.parse_args()

    projector = MinutesProjector()

    try:
        projector.connect()
        projector.run(update_db=args.update, min_games=args.min_games)

    except (psycopg2.Error, KeyError, TypeError, ValueError) as e:
        logger.error(f"Failed: {e}")
        raise
    finally:
        projector.close()


if __name__ == "__main__":
    main()
