"""
Pitch-Mix Rolling Feature Extractor (MLB - STRIKEOUTS)
======================================================
Extracts a pitcher's rolling L10 pitch-mix profile (per-pitch-group whiff
rates, spin, fastball velo, zone rate, first-strike rate) as of a game date.

These come from raw Baseball Savant pitches aggregated into
``mlb_players.statcast_pitcher_pitchmix_games`` by
``mlb.scripts.loaders.load_statcast_pitchmix``. They are the underlying
arsenal-quality signals that drive strikeout totals - features the
aggregate-only ``statcast_pitcher_games`` table cannot express.

Rolling window: L10 (last 10 games strictly BEFORE ``game_date``). Leakage-safe
(``game_date < %s``) - the target game's own outcome never enters the vector.

Mirrors the BaseFeatureExtractor interface (``extract`` / ``get_defaults`` /
``FEATURE_NAMES``).
"""

import logging
from typing import Any, Dict, List

import pandas as pd

from .base import BaseFeatureExtractor

logger = logging.getLogger(__name__)

PITCHER_STATS = frozenset({"STRIKEOUTS"})


class PitchMixFeatureExtractor(BaseFeatureExtractor):
    """Rolling L10 pitch-mix features for a pitcher (STRIKEOUTS)."""

    WINDOW = 10  # L10

    # Source metric columns in statcast_pitcher_pitchmix_games.
    _METRICS = (
        "num_pitch_types",
        "fastball_velo",
        "fastball_spin",
        "breaking_spin",
        "zone_rate",
        "first_strike_rate",
        "fastball_whiff_rate",
        "slider_whiff_rate",
        "curve_whiff_rate",
        "changeup_whiff_rate",
    )

    # League-ish neutral defaults (used when a pitcher has no pitch-mix history).
    _DEFAULTS = {
        "num_pitch_types": 4.0,
        "fastball_velo": 93.5,
        "fastball_spin": 2250.0,
        "breaking_spin": 2450.0,
        "zone_rate": 0.48,
        "first_strike_rate": 0.60,
        "fastball_whiff_rate": 0.20,
        "slider_whiff_rate": 0.32,
        "curve_whiff_rate": 0.30,
        "changeup_whiff_rate": 0.30,
    }

    FEATURE_NAMES = tuple(f"pitchmix_{m}_L10" for m in _METRICS) + ("pitchmix_games_sample",)

    def __init__(self, conn: Any):
        """
        Args:
            conn: Connection to the ``mlb_players`` schema
                  (statcast_pitcher_pitchmix_games + pitcher_profile).
        """
        super().__init__(conn, name="PitchMixFeatures")

    @classmethod
    def get_defaults(cls) -> Dict[str, float]:
        d = {f"pitchmix_{m}_L10": cls._DEFAULTS[m] for m in cls._METRICS}
        d["pitchmix_games_sample"] = 0.0
        return d

    def extract(
        self,
        player_name: str,
        game_date: Any,
        stat_type: str = "STRIKEOUTS",
        **kwargs,
    ) -> Dict[str, float]:
        """
        Extract rolling L10 pitch-mix features for a pitcher as of ``game_date``.

        Args:
            player_name: Pitcher full name.
            game_date: Date of the upcoming game (leakage cutoff, exclusive).
            stat_type: STRIKEOUTS (only pitcher stat; others still return rows
                    when a ``player_id`` is given - the table is pitcher-only).
            **kwargs: ``player_id`` may be passed to skip the name lookup.

        Returns:
            Dict of rolling pitch-mix features (defaults when no history).
        """
        game_date_str = self._normalize_date(game_date)
        cols = ", ".join(f"pm.{m}" for m in self._METRICS)

        player_id = kwargs.get("player_id")
        if player_id is not None:
            query = f"""
                SELECT pm.game_date, {cols}
                FROM statcast_pitcher_pitchmix_games pm
                WHERE pm.player_id = %s AND pm.game_date < %s
                ORDER BY pm.game_date DESC
                LIMIT 20
            """
            params: tuple = (player_id, game_date_str)
        else:
            query = f"""
                SELECT pm.game_date, {cols}
                FROM statcast_pitcher_pitchmix_games pm
                JOIN pitcher_profile pr ON pm.player_id = pr.player_id
                WHERE unaccent(pr.full_name) = unaccent(%s)
                  AND pm.game_date < %s
                ORDER BY pm.game_date DESC
                LIMIT 20
            """
            params = (player_name, game_date_str)

        df = self._safe_query(query, params)
        if df is None or len(df) == 0:
            return self.get_defaults()

        return self._compute_features(df)

    def extract_from_games(self, games: List[Dict], game_date: Any) -> Dict[str, float]:
        """
        Extract from pre-fetched per-game pitch-mix dicts (no query: the caller supplies the rows).

        Args:
            games: List of per-game metric dicts (most-recent-first).
            game_date: Upcoming game date (unused; rows are pre-filtered).

        Returns:
            Dict of rolling pitch-mix features.
        """
        if not games:
            return self.get_defaults()
        return self._compute_features(pd.DataFrame(games))

    # ------------------------------------------------------------------

    def _compute_features(self, df: pd.DataFrame) -> Dict[str, float]:
        """Compute L10 rolling means from a game-row DataFrame (date DESC)."""
        features = self.get_defaults()
        features["pitchmix_games_sample"] = float(len(df))

        for m in self._METRICS:
            if m not in df.columns:
                continue
            vals = pd.to_numeric(df[m], errors="coerce").iloc[: self.WINDOW].dropna().tolist()
            if vals:
                features[f"pitchmix_{m}_L10"] = float(sum(vals) / len(vals))

        return features
