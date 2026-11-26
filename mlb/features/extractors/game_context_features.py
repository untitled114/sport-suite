"""
Game Context Feature Extractor (MLB)
====================================
Extracts game-level context that is not captured by player rolling stats:

- park_factor: hitter-friendliness of the venue (from ``mlb_games.games``)
- is_home: home/away flag (home batters bat last; pitchers' park matters)
- run_environment: park factor combined with home/away orientation
- venue_known: whether the game/venue was resolved

Weather-agnostic by design (the MLB Stats API does not reliably expose
historical weather; park_factor absorbs most venue run-scoring effects).

Source: ``mlb_games.games`` (game_id, venue, park_factor, home/away teams).
"""

import logging
from typing import Any, Dict, Optional

from .base import BaseFeatureExtractor

logger = logging.getLogger(__name__)

# League-average park factor (100 = neutral on the standard scale)
NEUTRAL_PARK_FACTOR = 100.0


class GameContextFeatureExtractor(BaseFeatureExtractor):
    """Park-factor, home/away and run-environment context from the games table."""

    FEATURE_NAMES = (
        "park_factor",
        "is_home",
        "run_environment",
        "venue_known",
    )

    def __init__(self, games_conn: Any):
        """
        Args:
            games_conn: Connection to ``mlb_games`` (games table).
        """
        super().__init__(games_conn, name="GameContext")

    @classmethod
    def get_defaults(cls) -> Dict[str, float]:
        return {
            "park_factor": NEUTRAL_PARK_FACTOR,
            "is_home": 1.0,
            "run_environment": 1.0,  # park_factor as a ratio (the stored factor is on a 100 scale)
            "venue_known": 0.0,
        }

    def extract(
        self,
        player_name: str,
        game_date: Any,
        stat_type: str = "HITS",
        team: str = None,
        opponent: str = None,
        is_home: Optional[bool] = None,
        **kwargs,
    ) -> Dict[str, float]:
        """
        Extract game context features.

        Args:
            player_name: Player full name (unused; interface consistency)
            game_date: Game date
            stat_type: Stat type (interface consistency)
            team: Player's team abbreviation
            opponent: Opposing team abbreviation
            is_home: Whether the player's team is home (if known up front)

        Returns:
            Dict of game-context features
        """
        game_date_str = self._normalize_date(game_date)
        features = self.get_defaults()

        if is_home is not None:
            features["is_home"] = 1.0 if is_home else 0.0

        if team and opponent:
            self._lookup_game(team, opponent, game_date_str, features)

        return self.compute_derived(features)

    def _lookup_game(
        self,
        team: str,
        opponent: str,
        game_date_str: str,
        features: Dict[str, float],
    ) -> None:
        """Resolve the game row and park factor for this matchup/date."""
        query = """
            SELECT home_team, away_team, park_factor
            FROM games
            WHERE game_date = %s
              AND (
                (home_team = %s AND away_team = %s)
                OR (home_team = %s AND away_team = %s)
              )
            LIMIT 1
        """
        df = self._safe_query(
            query,
            (game_date_str, team, opponent, opponent, team),
        )
        if df is None or len(df) == 0:
            return

        row = df.iloc[0]
        features["venue_known"] = 1.0
        pf = self._safe_float(row.get("park_factor"), NEUTRAL_PARK_FACTOR)
        if pf <= 0:
            pf = NEUTRAL_PARK_FACTOR
        features["park_factor"] = pf

        home_team = row.get("home_team")
        if home_team is not None:
            features["is_home"] = 1.0 if home_team == team else 0.0

    def compute_derived(self, features: Dict[str, float]) -> Dict[str, float]:
        """Compute derived context features from park factor + home/away."""
        features = self.validate_features(features)
        features["run_environment"] = features["park_factor"] / 100.0
        return features

    def from_park_factor(
        self, park_factor: Optional[float], is_home: Optional[bool], venue_known: bool = True
    ) -> Dict[str, float]:
        """
        Build features directly from pre-fetched values (no query: the caller supplies the rows).

        Args:
            park_factor: Venue park factor (100 = neutral)
            is_home: Home/away flag
            venue_known: Whether the venue/game was resolved

        Returns:
            Dict of game-context features
        """
        features = self.get_defaults()
        if park_factor is not None and park_factor > 0:
            features["park_factor"] = float(park_factor)
            features["venue_known"] = 1.0 if venue_known else 0.0
        if is_home is not None:
            features["is_home"] = 1.0 if is_home else 0.0
        return self.compute_derived(features)
