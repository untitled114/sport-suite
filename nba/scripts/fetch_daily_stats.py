#!/usr/bin/env python3
"""
Daily NBA Stats Updater
========================
Fetches the latest NBA box scores and stores each player's game log.

Usage:
    # Fetch yesterday's games (default)
    python3 nba/scripts/fetch_daily_stats.py

    # Fetch specific date
    python3 nba/scripts/fetch_daily_stats.py --date 2025-10-29

    # Fetch last N days
    python3 nba/scripts/fetch_daily_stats.py --days 3
"""

import argparse
import json
import logging
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import psycopg2
import requests

from nba.config.database import get_games_db_config, get_players_db_config
from nba.scripts.game_start_times import fill_start_times, scoreboard_starts
from nba.utils.player_lookup import PlayerLookup
from nba.utils.season import season_for_date
from nba.utils.team_utils import VALID_NBA_TEAMS, normalize_team_abbrev

EST = ZoneInfo("America/New_York")

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

# Database configs
DB_PLAYERS = get_players_db_config()
DB_GAMES = get_games_db_config()

# ESPN NBA API
ESPN_SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard"
ESPN_BOXSCORE_URL = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/summary"


def is_nba_game(home_abbrev, away_abbrev) -> bool:
    """Whether both sides are NBA teams. The All-Star game and its events (sides named STARS,
    STRIPES, WORLD) come on the same scoreboard; stored as games they would give players a box
    score and a rest day they never had."""
    return home_abbrev in VALID_NBA_TEAMS and away_abbrev in VALID_NBA_TEAMS


def parse_player_stats(player_stats, labels):
    """A player's box-score line from an ESPN summary, or None for a DNP or unusable line.

    Returns minutes_played .. plus_minus in player_game_logs' column order. Shared by the daily
    loader and scripts/ops/backfill_player_box_scores.py.
    """
    stats_array = player_stats.get("stats", [])
    stats_dict = dict(zip(labels, stats_array)) if labels and stats_array else {}

    def parse_made_attempted(val):
        """Parse ESPN 'made-attempted' format (e.g. '5-12' -> (5, 12))"""
        if not val or val == "--":
            return 0, 0
        parts = str(val).split("-")
        if len(parts) == 2:
            try:
                return int(parts[0]), int(parts[1])
            except (ValueError, TypeError):
                return 0, 0
        return 0, 0

    # Parse stats (skip season totals or invalid data)
    try:
        min_str = str(stats_dict.get("MIN", "0"))

        # Skip incomplete stats
        if "--" in min_str or min_str == "" or min_str == "None":
            return None

        minutes = float(min_str.split(":")[0] if ":" in min_str else min_str)

        # Skip DNPs and season totals
        if minutes == 0 or minutes > 60:
            return None

        points = int(stats_dict.get("PTS", 0) or 0)
        rebounds = int(stats_dict.get("REB", 0) or 0)
        assists = int(stats_dict.get("AST", 0) or 0)
        steals = int(stats_dict.get("STL", 0) or 0)
        blocks = int(stats_dict.get("BLK", 0) or 0)
        turnovers = int(stats_dict.get("TO", 0) or 0)

        # ESPN uses "made-attempted" format: FG="5-12", 3PT="2-5", FT="3-4"
        fgm, fga = parse_made_attempted(stats_dict.get("FG"))
        threes, three_att = parse_made_attempted(stats_dict.get("3PT"))
        ftm, fta = parse_made_attempted(stats_dict.get("FT"))

        plus_minus_raw = stats_dict.get("+/-", stats_dict.get("PLUSMINUS", 0))
        try:
            plus_minus = int(float(plus_minus_raw or 0))
        except (ValueError, TypeError):
            plus_minus = 0

    except (ValueError, TypeError):
        return None

    return {
        "minutes_played": int(minutes),
        "points": points,
        "rebounds": rebounds,
        "assists": assists,
        "steals": steals,
        "blocks": blocks,
        "turnovers": turnovers,
        "three_pointers_made": threes,
        "three_pt_attempted": three_att,
        "fg_made": fgm,
        "fg_attempted": fga,
        "ft_made": ftm,
        "ft_attempted": fta,
        "plus_minus": plus_minus,
    }


class NBAStatsUpdater:
    """Fetch and update daily NBA stats"""

    def __init__(self):
        self.conn_players = None
        self.conn_games = None
        self._lookup = None
        self.session = requests.Session()
        self.session.headers.update(
            {"User-Agent": "sport-suite/3.0"}
        )

    def connect(self):
        """Connect to all databases"""
        self.conn_players = psycopg2.connect(**DB_PLAYERS)
        self.conn_games = psycopg2.connect(**DB_GAMES)
        logger.info("Connected to all NBA databases")

    def close(self):
        """Close all connections"""
        if self.conn_players:
            self.conn_players.close()
        if self.conn_games:
            self.conn_games.close()

    def get_games_for_date(self, date_str):
        """Fetch games for a specific date from ESPN API"""
        logger.info(f"\n{'='*100}")
        logger.info(f"FETCHING NBA GAMES FOR {date_str}")
        logger.info(f"{'='*100}")

        # Convert to ESPN date format (YYYYMMDD)
        date_obj = datetime.strptime(date_str, "%Y-%m-%d")
        espn_date = date_obj.strftime("%Y%m%d")

        params = {"dates": espn_date, "limit": 50}

        try:
            response = self.session.get(ESPN_SCOREBOARD_URL, params=params, timeout=15)
            response.raise_for_status()
            data = response.json()

            events = data.get("events", [])
            logger.info(f"Found {len(events)} games")

            return events

        except (psycopg2.Error, KeyError, TypeError, ValueError) as e:
            logger.error(f"Error fetching games: {e}")
            return []

    def get_boxscore(self, game_id):
        """Fetch detailed boxscore for a game"""
        params = {"event": game_id}

        try:
            response = self.session.get(ESPN_BOXSCORE_URL, params=params, timeout=15)
            response.raise_for_status()
            return response.json()

        except (psycopg2.Error, KeyError, TypeError, ValueError) as e:
            logger.error(f"Error fetching boxscore for game {game_id}: {e}")
            return None

    def map_player_to_db_id(self, espn_id, player_name, on_date=None):
        """Map an ESPN box-score name to the database player_id through players.name_key.

        player_profile keeps diacritics (Dončić) and ESPN often does not; two profiles sharing a
        name resolve to the current player (nba/utils/player_lookup.py), never to whichever
        LIMIT 1 returned.
        """
        if self._lookup is None:
            self._lookup = PlayerLookup(self.conn_players)
        player_id = self._lookup.resolve(player_name, on_date)
        if player_id is None:
            logger.warning(f"No DB mapping for {player_name} (ESPN ID: {espn_id})")
        return player_id

    def load_player_game(self, player_stats, game_info, labels):
        """Load player game into player_game_logs table"""
        espn_id = player_stats.get("athlete", {}).get("id")
        player_name = player_stats.get("athlete", {}).get("displayName", "")

        player_id = self.map_player_to_db_id(espn_id, player_name, game_info.get("game_date"))
        if not player_id:
            return False

        line = parse_player_stats(player_stats, labels)
        if line is None:
            return False
        points, rebounds, assists = line["points"], line["rebounds"], line["assists"]
        steals, blocks = line["steals"], line["blocks"]

        cursor = self.conn_players.cursor()

        season = season_for_date(game_info["game_date"])  # 2025-26 season = 2026

        game_data = {
            "player_id": player_id,
            "game_id": f"{game_info['game_date']}_{game_info['team']}",  # this loader's own id: date and team
            "game_date": game_info["game_date"],
            "season": season,  # 2025-26 season = 2026
            "team_abbrev": game_info["team"],
            "opponent_abbrev": game_info["opponent"],
            "is_home": game_info["is_home"],
            "minutes_played": line["minutes_played"],
            "points": points,
            "rebounds": rebounds,
            "assists": assists,
            "steals": steals,
            "blocks": blocks,
            "turnovers": line["turnovers"],
            "three_pointers_made": line["three_pointers_made"],
            "three_pt_attempted": line["three_pt_attempted"],
            "fg_made": line["fg_made"],
            "fg_attempted": line["fg_attempted"],
            "ft_made": line["ft_made"],
            "ft_attempted": line["ft_attempted"],
            "plus_minus": line["plus_minus"],
        }

        # Check if exists (use composite key: player_id + game_date)
        cursor.execute(
            """
            SELECT player_id FROM player_game_logs
            WHERE player_id = %s AND game_date = %s
        """,
            (player_id, game_data["game_date"]),
        )

        existing = cursor.fetchone()

        if existing:
            # Update existing
            cursor.execute(
                """
                UPDATE player_game_logs
                SET game_id = %s, season = %s, team_abbrev = %s, opponent_abbrev = %s, is_home = %s,
                    minutes_played = %s, points = %s, rebounds = %s, assists = %s,
                    steals = %s, blocks = %s, turnovers = %s,
                    three_pointers_made = %s, three_pt_attempted = %s,
                    fg_made = %s, fg_attempted = %s, ft_made = %s, ft_attempted = %s,
                    plus_minus = %s
                WHERE player_id = %s AND game_date = %s
            """,
                (
                    game_data["game_id"],
                    game_data["season"],
                    game_data["team_abbrev"],
                    game_data["opponent_abbrev"],
                    game_data["is_home"],
                    game_data["minutes_played"],
                    game_data["points"],
                    game_data["rebounds"],
                    game_data["assists"],
                    game_data["steals"],
                    game_data["blocks"],
                    game_data["turnovers"],
                    game_data["three_pointers_made"],
                    game_data["three_pt_attempted"],
                    game_data["fg_made"],
                    game_data["fg_attempted"],
                    game_data["ft_made"],
                    game_data["ft_attempted"],
                    game_data["plus_minus"],
                    player_id,
                    game_data["game_date"],
                ),
            )
            action = "updated"
        else:
            # Insert new
            cursor.execute(
                """
                INSERT INTO player_game_logs (
                    player_id, game_id, game_date, season, team_abbrev, opponent_abbrev, is_home,
                    minutes_played, points, rebounds, assists, steals, blocks, turnovers,
                    three_pointers_made, three_pt_attempted, fg_made, fg_attempted,
                    ft_made, ft_attempted, plus_minus
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
                tuple(game_data.values()),
            )
            action = "inserted"

        self.conn_players.commit()
        cursor.close()

        logger.info(
            f"  OK {player_name}: {action} ({points}p {rebounds}r {assists}a {steals}s {blocks}b)"
        )
        return True

    def record_start_times(self, game_date, events):
        """Fill games.games.start_time for game_date's existing rows from the scoreboard."""
        try:
            filled, unmatched = fill_start_times(
                self.conn_games, game_date, scoreboard_starts(events)
            )
            self.conn_games.commit()
        except psycopg2.Error as e:
            self.conn_games.rollback()
            logger.warning(f"start_time not recorded for {game_date}: {e}")
            return
        if filled:
            logger.info(f"  start_time filled for {filled} game(s) on {game_date}")
        for s in unmatched:
            logger.warning(
                f"  no games row for {s.away} @ {s.home} on {game_date} (ESPN {s.espn_id})"
            )

    def process_game(self, event, fetch_date):
        """Process a single game from ESPN data"""
        game_id = event.get("id")

        # Get team info
        competitions = event.get("competitions", [])
        if not competitions:
            return

        comp = competitions[0]

        # Only process completed games (STATUS_FINAL)
        status = comp.get("status", {}).get("type", {}).get("name", "")
        if status != "STATUS_FINAL":
            return

        competitors = comp.get("competitors", [])

        if len(competitors) != 2:
            return

        home_team = next((c for c in competitors if c.get("homeAway") == "home"), None)
        away_team = next((c for c in competitors if c.get("homeAway") == "away"), None)

        if not home_team or not away_team:
            return

        # Use fetch_date instead of ESPN's unreliable date field
        game_date = fetch_date

        # Normalize team abbreviations
        home_abbrev = normalize_team_abbrev(home_team["team"]["abbreviation"])
        away_abbrev = normalize_team_abbrev(away_team["team"]["abbreviation"])

        if not is_nba_game(home_abbrev, away_abbrev):
            logger.info(
                f"\n  Skipped (not two NBA teams): {away_abbrev} @ {home_abbrev} ({game_date})"
            )
            return

        logger.info(f"\nProcessing: {away_abbrev} @ {home_abbrev} ({game_date})")

        # Fetch boxscore
        boxscore = self.get_boxscore(game_id)
        if not boxscore:
            return

        time.sleep(0.5)  # Rate limiting

        # Process box score data
        boxscore_data = boxscore.get("boxscore", {})
        players = boxscore_data.get("players", [])

        player_count = 0

        for team_data in players:
            team_abbrev_raw = team_data.get("team", {}).get("abbreviation", "")
            team_abbrev = normalize_team_abbrev(team_abbrev_raw)

            is_home = team_abbrev == home_abbrev
            opponent = away_abbrev if is_home else home_abbrev

            game_info = {
                "game_date": game_date,
                "team": team_abbrev,
                "opponent": opponent,
                "is_home": is_home,
            }

            # Process players
            for stat_group in team_data.get("statistics", []):
                labels = stat_group.get("labels", [])

                for player in stat_group.get("athletes", []):
                    if self.load_player_game(player, game_info, labels):
                        player_count += 1

        logger.info(f"  Loaded: {player_count} players")

    def run(self, start_date, days=1):
        """Main execution"""
        logger.info(f"\n{'='*100}")
        logger.info("NBA DAILY STATS UPDATER")
        logger.info(f"{'='*100}")
        logger.info(f"Fetching stats from {start_date} ({days} day(s))")

        self.connect()

        total_games = 0
        fetched = set()

        for i in range(days):
            date_obj = datetime.strptime(start_date, "%Y-%m-%d") + timedelta(days=i)
            date_str = date_obj.strftime("%Y-%m-%d")

            events = self.get_games_for_date(date_str)
            self.record_start_times(date_obj.date(), events)
            fetched.add(date_obj.date())

            for event in events:
                self.process_game(event, date_str)
                total_games += 1

        # Today's start times too (one more scoreboard), so they are known before the games
        today = datetime.now(EST).date()
        if today not in fetched:
            self.record_start_times(today, self.get_games_for_date(today.isoformat()))

        logger.info(f"\n{'='*100}")
        logger.info(f"COMPLETE - Processed {total_games} games")
        logger.info(f"{'='*100}")

        self.close()


def main():
    parser = argparse.ArgumentParser(description="Fetch latest NBA player stats")
    parser.add_argument(
        "--date", type=str, help="Specific date (YYYY-MM-DD), defaults to yesterday"
    )
    parser.add_argument("--days", type=int, default=1, help="Number of days to fetch (default: 1)")

    args = parser.parse_args()

    # Default to yesterday (games completed)
    if args.date:
        start_date = args.date
    else:
        yesterday = datetime.now(EST) - timedelta(days=1)
        start_date = yesterday.strftime("%Y-%m-%d")

    updater = NBAStatsUpdater()
    updater.run(start_date, args.days)


if __name__ == "__main__":
    main()
