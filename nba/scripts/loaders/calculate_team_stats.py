#!/usr/bin/env python3
"""
Calculate Team Season Stats from Games Data
============================================
Calculates team_season_stats including:
- Pace (average from team_game_logs)
- Offensive Rating (Points per 100 possessions)
- Defensive Rating (Points allowed per 100 possessions)

Uses games table to get opponent points for defensive rating.

Usage:
    python calculate_team_stats.py --season 2020 2021 2022 2023 2024
"""

import argparse
import logging
import sys
from pathlib import Path

import psycopg2
from psycopg2.extras import execute_values

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from nba.config.database import get_games_db_config, get_team_db_config
from nba.sql import named_query

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# Database connections
GAMES_DB = get_games_db_config()
TEAM_DB = get_team_db_config()


# Regular-season games only. A season loaded before games.season_type existed has no typed rows;
# those seasons were loaded from the regular season alone, so all their games count.
REGULAR_SEASON = """
    (g.season_type = 'Regular Season'
     OR NOT EXISTS (SELECT 1 FROM games t WHERE t.season = g.season AND t.season_type IS NOT NULL))
"""


def calculate_team_season_stats(seasons: list):
    """Calculate and insert team season stats from games data"""

    games_conn = psycopg2.connect(**GAMES_DB)
    team_conn = psycopg2.connect(**TEAM_DB)

    try:
        for season in seasons:
            logger.info(f"Calculating team stats for season {season}...")

            games_cur = games_conn.cursor()

            # Compute pace per team from team_game_logs box score columns.
            # Formula: possessions are estimated as FGA + TOV + 0.44 * FTA (no OREB column available)
            # We average (team_poss + opp_poss) / 2 per game, which is the NBA pace definition.
            games_cur.execute(
                named_query("team_season_pace_and_shooting"),
                (season,),
            )
            pace_by_team = {
                row[0]: {
                    "games": row[1],
                    "pace": float(row[2]),
                    "ts_pct": float(row[3]) if row[3] is not None else None,
                    "reb_pct": float(row[4]) if row[4] is not None else None,
                }
                for row in games_cur.fetchall()
            }
            # Regular-season record from final scores (a game counts once both scores are in)
            games_cur.execute(
                named_query("team_season_record"),
                (season, season),
            )
            record = {row[0]: (row[1], row[2], row[3]) for row in games_cur.fetchall()}

            if not pace_by_team:
                logger.warning(f"No teams found for season {season}")
                continue

            insert_data = []

            for team, pace_data in pace_by_team.items():
                avg_pace = pace_data["pace"]

                # Get scoring data: home games
                games_cur.execute(
                    f"""
                    SELECT
                        COUNT(*) as games,
                        AVG(g.home_score) as avg_points_scored,
                        AVG(g.away_score) as avg_points_allowed
                    FROM games g
                    WHERE g.home_team = %s
                      AND g.season = %s
                      AND g.home_score IS NOT NULL
                      AND {REGULAR_SEASON}
                """,
                    (team, season),
                )
                home_data = games_cur.fetchone()

                # Away games
                games_cur.execute(
                    f"""
                    SELECT
                        COUNT(*) as games,
                        AVG(g.away_score) as avg_points_scored,
                        AVG(g.home_score) as avg_points_allowed
                    FROM games g
                    WHERE g.away_team = %s
                      AND g.season = %s
                      AND g.away_score IS NOT NULL
                      AND {REGULAR_SEASON}
                """,
                    (team, season),
                )
                away_data = games_cur.fetchone()

                home_games = home_data[0] or 0
                away_games = away_data[0] or 0
                total_games = home_games + away_games

                if total_games == 0:
                    logger.warning(f"No games found for {team} in {season}")
                    continue

                avg_points_scored = float(
                    ((home_data[1] or 0) * home_games + (away_data[1] or 0) * away_games)
                    / total_games
                )

                avg_points_allowed = float(
                    ((home_data[2] or 0) * home_games + (away_data[2] or 0) * away_games)
                    / total_games
                )

                # Offensive/Defensive Rating = Points per 100 possessions
                offensive_rating = (avg_points_scored / avg_pace) * 100 if avg_pace > 0 else None
                defensive_rating = (avg_points_allowed / avg_pace) * 100 if avg_pace > 0 else None

                played, wins, losses = record.get(team, (None, None, None))
                net_rating = (
                    round(offensive_rating - defensive_rating, 2)
                    if offensive_rating is not None and defensive_rating is not None
                    else None
                )
                ts_pct, reb_pct = pace_data["ts_pct"], pace_data["reb_pct"]
                insert_data.append(
                    (
                        team,
                        season,
                        played,
                        wins,
                        losses,
                        net_rating,
                        round(ts_pct, 4) if ts_pct is not None else None,
                        round(reb_pct, 4) if reb_pct is not None else None,
                        round(avg_pace, 2),
                        round(offensive_rating, 2) if offensive_rating else None,
                        round(defensive_rating, 2) if defensive_rating else None,
                        None,  # def_rating_vs_pg (requires positional data)
                        None,  # def_rating_vs_sg
                        None,  # def_rating_vs_sf
                        None,  # def_rating_vs_pf
                        None,  # def_rating_vs_c
                        None,  # pace_neutral_off_rating
                    )
                )

                logger.debug(
                    f"  {team}: {total_games} games, Pace={avg_pace:.1f}, OffRtg={offensive_rating:.1f}, DefRtg={defensive_rating:.1f}"
                )

            # Insert into team database
            if insert_data:
                team_cur = team_conn.cursor()
                insert_query = """
                    INSERT INTO team_season_stats
                    (team_abbrev, season, games_played, wins, losses, net_rating,
                     true_shooting_pct, rebounding_pct,
                     pace, offensive_rating, defensive_rating,
                     def_rating_vs_pg, def_rating_vs_sg, def_rating_vs_sf,
                     def_rating_vs_pf, def_rating_vs_c, pace_neutral_off_rating)
                    VALUES %s
                    ON CONFLICT (team_abbrev, season)
                    DO UPDATE SET
                        games_played = EXCLUDED.games_played,
                        wins = EXCLUDED.wins,
                        losses = EXCLUDED.losses,
                        net_rating = EXCLUDED.net_rating,
                        true_shooting_pct = EXCLUDED.true_shooting_pct,
                        rebounding_pct = EXCLUDED.rebounding_pct,
                        pace = EXCLUDED.pace,
                        offensive_rating = EXCLUDED.offensive_rating,
                        defensive_rating = EXCLUDED.defensive_rating
                """

                execute_values(team_cur, insert_query, insert_data)
                team_conn.commit()

                logger.info(
                    f"Calculated and inserted {len(insert_data)} team season stats for {season}"
                )
            else:
                logger.warning(f"No team stats calculated for {season}")

    except (psycopg2.Error, KeyError, TypeError, ValueError) as e:
        logger.error(f"Error calculating team stats: {e}")
        raise
    finally:
        games_conn.close()
        team_conn.close()


def _default_seasons() -> list[int]:
    """Compute default season range through the current NBA season.

    Uses END-year convention to match team_game_logs.season.
    For March 2026 -> current season is 2026 -> range [2020, 2026].
    """
    from nba.utils.season import current_season

    return list(range(2020, current_season() + 1))


def main():
    parser = argparse.ArgumentParser(description="Calculate team season stats from game logs")
    parser.add_argument(
        "--season",
        type=int,
        nargs="+",
        default=None,
        help="Seasons to calculate (END year, e.g., 2025 for 2024-25). Defaults to 2020-current.",
    )

    args = parser.parse_args()

    if args.season is None:
        args.season = _default_seasons()

    logger.info("Starting team season stats calculation...")
    logger.info(f"Seasons: {args.season}")
    calculate_team_season_stats(args.season)
    logger.info("Team season stats calculation complete!")


if __name__ == "__main__":
    main()
