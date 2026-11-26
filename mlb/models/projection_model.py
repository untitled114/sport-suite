"""
Park-Adjusted Projection Model (MLB)
====================================
A stat projection from park-adjusted rolling averages and opponent strength.

Projects a player's expected stat value by:
1. Weighted rolling averages (L5/L10/L20) from ``mlb_players``.
2. Park adjustment (venue park_factor vs league-neutral 1.0).
3. Opponent strength adjustment:
   - Batter stat types (HITS, TOTAL_BASES): opposing pitching staff strength.
   - Pitcher stat type (STRIKEOUTS): opposing lineup strikeout tendency.
4. Home/away split adjustment.

The projection is the expected value of the stat for that game.

Usage:
    from mlb.models.projection_model import ProjectionModel
    model = ProjectionModel()
    projected = model.project("Aaron Judge", "TOTAL_BASES", "BOS",
                              is_home=True, park_factor=1.05)
"""

import logging
from typing import Any, Dict, Optional

from mlb.config.database import get_connection
from mlb.sql import named_query

logger = logging.getLogger(__name__)

# Stat type -> stat column in the rolling stats / game-log tables.
STAT_COLUMN_MAP = {
    "STRIKEOUTS": "strikeouts",
    "HITS": "hits",
    "TOTAL_BASES": "total_bases",
}

# Batter vs pitcher role per stat type (determines which log table is queried).
PITCHER_STATS = ("STRIKEOUTS",)

# League-neutral park factor (1.0 = neutral; >1 hitter-friendly).
NEUTRAL_PARK_FACTOR = 1.0

# Home/away adjustment factors (small; MLB home advantage is modest).
HOME_ADVANTAGE = {
    "STRIKEOUTS": 0.005,
    "HITS": 0.010,
    "TOTAL_BASES": 0.012,
}

# Rolling-window weights (recent games weighted more heavily).
ROLLING_WEIGHTS = {"L5": 0.50, "L10": 0.30, "L20": 0.20}


def _stat_column(stat_type: str) -> str:
    """The game-log column for a stat type; an unknown stat type is an error, never SQL text."""
    try:
        return STAT_COLUMN_MAP[stat_type]
    except KeyError:
        raise ValueError(
            f"unknown stat type {stat_type!r}; known: {sorted(STAT_COLUMN_MAP)}"
        ) from None


class ProjectionModel:
    """Park-adjusted projection model using rolling averages."""

    def __init__(self):
        self._players_conn = None
        self._teams_conn = None

    def _get_players_conn(self):
        if self._players_conn is None or self._players_conn.closed:
            self._players_conn = get_connection("mlb_players", autocommit=True)
        return self._players_conn

    def _get_teams_conn(self):
        if self._teams_conn is None or self._teams_conn.closed:
            self._teams_conn = get_connection("mlb_teams", autocommit=True)
        return self._teams_conn

    def project(
        self,
        player_name: str,
        stat_type: str,
        opponent_team: str,
        is_home: bool = True,
        park_factor: Optional[float] = None,
        as_of: Optional[Any] = None,
    ) -> float:
        """Project expected stat value for a player.

        Args:
            player_name: Player full name (diacritics handled via unaccent).
            stat_type: STRIKEOUTS, HITS or TOTAL_BASES.
            opponent_team: Opponent team abbreviation.
            is_home: Whether the player's team is at home.
            park_factor: Venue park factor (defaults to neutral if None).
            as_of: Use only games strictly before this date (default: every game played).

        Returns:
            Projected stat value (float), rounded to 1 decimal.
        """
        details = self.project_with_details(
            player_name, stat_type, opponent_team, is_home, park_factor, as_of
        )
        return details["projection"]

    def project_with_details(
        self,
        player_name: str,
        stat_type: str,
        opponent_team: str,
        is_home: bool = True,
        park_factor: Optional[float] = None,
        as_of: Optional[Any] = None,
    ) -> Dict:
        """Project with a full breakdown of component factors."""
        stat_type = stat_type.upper()
        stat_col = _stat_column(stat_type)
        is_pitcher = stat_type in PITCHER_STATS

        rolling = self._get_player_rolling_stats(player_name, stat_col, is_pitcher, as_of)

        base_projection = (
            rolling.get("L5", 0) * ROLLING_WEIGHTS["L5"]
            + rolling.get("L10", 0) * ROLLING_WEIGHTS["L10"]
            + rolling.get("L20", 0) * ROLLING_WEIGHTS["L20"]
        )

        park = self._resolve_park_factor(park_factor, stat_type)
        opp_factor = self._get_opponent_factor(opponent_team, stat_type, is_pitcher)
        home_factor = (
            1.0 + HOME_ADVANTAGE[stat_type] if is_home else 1.0 - HOME_ADVANTAGE[stat_type]
        )

        projected = base_projection * park * opp_factor * home_factor

        return {
            "player_name": player_name,
            "stat_type": stat_type,
            "opponent_team": opponent_team,
            "is_home": is_home,
            "is_pitcher": is_pitcher,
            "projection": round(projected, 1),
            "base_projection": round(base_projection, 2),
            "rolling_L5": rolling.get("L5", 0),
            "rolling_L10": rolling.get("L10", 0),
            "rolling_L20": rolling.get("L20", 0),
            "std_dev": rolling.get("std", 0),
            "park_factor": round(park, 3),
            "opponent_factor": round(opp_factor, 3),
            "home_factor": round(home_factor, 3),
            "team": rolling.get("team", ""),
        }

    def get_player_std_dev(self, player_name: str, stat_type: str) -> float:
        """Estimate the std dev of a player's recent performance for a stat type."""
        stat_type = stat_type.upper()
        stat_col = _stat_column(stat_type)
        is_pitcher = stat_type in PITCHER_STATS
        rolling = self._get_player_rolling_stats(player_name, stat_col, is_pitcher)
        return rolling.get("std", 1.5)

    # ---------------------------------------------------------------- helpers
    def _resolve_park_factor(self, park_factor: Optional[float], stat_type: str) -> float:
        """Clamp a provided park factor, or return neutral.

        Pitcher strikeouts are largely park-independent, so we damp the park
        effect toward neutral for STRIKEOUTS.
        """
        if park_factor is None:
            return NEUTRAL_PARK_FACTOR
        pf = max(0.85, min(1.15, float(park_factor)))
        if stat_type in PITCHER_STATS:
            # Damp park influence for strikeouts (50% toward neutral).
            pf = NEUTRAL_PARK_FACTOR + (pf - NEUTRAL_PARK_FACTOR) * 0.5
        return pf

    def _get_player_rolling_stats(
        self, player_name: str, stat_col: str, is_pitcher: bool, as_of: Optional[Any] = None
    ) -> Dict:
        """L5/L10/L20 rolling averages from the game-log table, optionally strictly before a date."""
        if stat_col not in STAT_COLUMN_MAP.values():
            raise ValueError(f"no rolling stats for column {stat_col!r}")
        table = "pitcher_game_logs" if is_pitcher else "batter_game_logs"
        query = named_query("player_rolling_stat").format(table=table, stat_col=stat_col)
        conn = self._get_players_conn()
        with conn.cursor() as cur:
            cur.execute(query, (player_name, as_of, as_of))
            row = cur.fetchone()

        if not row or row[0] is None:
            logger.warning("No rolling stats for %s (%s)", player_name, stat_col)
            return {"L5": 0.0, "L10": 0.0, "L20": 0.0, "std": 1.5, "team": ""}

        return {
            "L5": float(row[0] or 0),
            "L10": float(row[1] or row[0] or 0),
            "L20": float(row[2] or row[1] or row[0] or 0),
            "std": float(row[3]) if row[3] is not None else 1.5,
            "team": row[4] or "",
        }

    def _get_opponent_factor(self, opponent_team: str, stat_type: str, is_pitcher: bool) -> float:
        """Opponent strength adjustment.

        Batters: an opposing staff that allows more runs than the league -> higher projection.
        Pitchers (STRIKEOUTS): a lineup that strikes out more than the league -> more strikeouts.
        The ratio to the league average is clamped to 0.90 .. 1.10.
        """
        if not opponent_team:
            return 1.0
        try:
            if is_pitcher:
                # The opposing lineup's strikeout rate, from the batter game logs.
                conn = self._get_players_conn()
                query = named_query("opponent_strikeout_rate_vs_league")
            else:
                # The opposing staff's runs allowed per game, from the team season table.
                conn = self._get_teams_conn()
                query = named_query("opponent_runs_allowed_vs_league")
            with conn.cursor() as cur:
                cur.execute(query, (opponent_team,))
                row = cur.fetchone()
        except Exception as e:
            logger.debug("Opponent factor lookup failed for %s: %s", opponent_team, e)
            return 1.0

        if not row or row[0] is None or not row[1]:
            return 1.0

        value = float(row[0])
        league_avg = float(row[1])
        if league_avg == 0:  # pragma: no cover - unreachable: `not row[1]` above already catches 0
            return 1.0
        factor = value / league_avg
        return max(0.90, min(1.10, factor))

    def close(self):
        """Close open database connections."""
        for conn in (self._players_conn, self._teams_conn):
            if conn and not conn.closed:
                conn.close()

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass
