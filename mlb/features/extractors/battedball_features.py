"""
Batted-Ball Rolling Feature Extractor (MLB - HITS / TOTAL_BASES)
===============================================================
Extracts a batter's rolling L10 batted-ball profile (ground-ball / fly-ball /
line-drive / popup rates + pull rate) as of a game date.

These come from raw Baseball Savant pitches aggregated into
``mlb_players.statcast_batter_battedball_games`` by
``mlb.scripts.loaders.load_statcast_pitchmix``. Batted-ball mix is the
underlying contact-quality signal driving HITS / TOTAL_BASES - line-drive and
pull tendencies translate to hits and extra bases. These are features the
aggregate-only ``statcast_batter_games`` table cannot express.

Rolling window: L10 (last 10 games strictly BEFORE ``game_date``). Leakage-safe
(``game_date < %s``).

Mirrors the BaseFeatureExtractor interface.
"""

import logging
from typing import Any, Dict, List

import pandas as pd

from .base import BaseFeatureExtractor

logger = logging.getLogger(__name__)

BATTER_STATS = frozenset({"HITS", "TOTAL_BASES"})


class BattedBallFeatureExtractor(BaseFeatureExtractor):
    """Rolling L10 batted-ball profile for a batter (HITS / TOTAL_BASES)."""

    WINDOW = 10  # L10

    # Source metric columns in statcast_batter_battedball_games.
    _METRICS = ("gb_rate", "fb_rate", "ld_rate", "popup_rate", "pull_rate")

    # League-ish neutral defaults (used when a batter has no batted-ball history).
    _DEFAULTS = {
        "gb_rate": 0.44,
        "fb_rate": 0.36,
        "ld_rate": 0.20,
        "popup_rate": 0.07,
        "pull_rate": 0.40,
    }

    FEATURE_NAMES = tuple(f"battedball_{m}_L10" for m in _METRICS) + ("battedball_games_sample",)

    def __init__(self, conn: Any):
        """
        Args:
            conn: Connection to the ``mlb_players`` schema
                  (statcast_batter_battedball_games + batter_profile).
        """
        super().__init__(conn, name="BattedBallFeatures")

    @classmethod
    def get_defaults(cls) -> Dict[str, float]:
        d = {f"battedball_{m}_L10": cls._DEFAULTS[m] for m in cls._METRICS}
        d["battedball_games_sample"] = 0.0
        return d

    def extract(
        self,
        player_name: str,
        game_date: Any,
        stat_type: str = "HITS",
        **kwargs,
    ) -> Dict[str, float]:
        """
        Extract rolling L10 batted-ball features for a batter as of ``game_date``.

        Args:
            player_name: Batter full name.
            game_date: Date of the upcoming game (leakage cutoff, exclusive).
            stat_type: HITS / TOTAL_BASES (batter stats).
            **kwargs: ``player_id`` may be passed to skip the name lookup.

        Returns:
            Dict of rolling batted-ball features (defaults when no history).
        """
        game_date_str = self._normalize_date(game_date)
        cols = ", ".join(f"bb.{m}" for m in self._METRICS)

        player_id = kwargs.get("player_id")
        if player_id is not None:
            query = f"""
                SELECT bb.game_date, {cols}
                FROM statcast_batter_battedball_games bb
                WHERE bb.player_id = %s AND bb.game_date < %s
                ORDER BY bb.game_date DESC
                LIMIT 20
            """
            params: tuple = (player_id, game_date_str)
        else:
            query = f"""
                SELECT bb.game_date, {cols}
                FROM statcast_batter_battedball_games bb
                JOIN batter_profile pr ON bb.player_id = pr.player_id
                WHERE unaccent(pr.full_name) = unaccent(%s)
                  AND bb.game_date < %s
                ORDER BY bb.game_date DESC
                LIMIT 20
            """
            params = (player_name, game_date_str)

        df = self._safe_query(query, params)
        if df is None or len(df) == 0:
            return self.get_defaults()

        return self._compute_features(df)

    def extract_from_games(self, games: List[Dict], game_date: Any) -> Dict[str, float]:
        """
        Extract from pre-fetched per-game batted-ball dicts (no query: the caller supplies the rows).

        Args:
            games: List of per-game metric dicts (most-recent-first).
            game_date: Upcoming game date (unused; rows are pre-filtered).

        Returns:
            Dict of rolling batted-ball features.
        """
        if not games:
            return self.get_defaults()
        return self._compute_features(pd.DataFrame(games))

    # ------------------------------------------------------------------

    def _compute_features(self, df: pd.DataFrame) -> Dict[str, float]:
        """Compute L10 rolling means from a game-row DataFrame (date DESC)."""
        features = self.get_defaults()
        features["battedball_games_sample"] = float(len(df))

        for m in self._METRICS:
            if m not in df.columns:
                continue
            vals = pd.to_numeric(df[m], errors="coerce").iloc[: self.WINDOW].dropna().tolist()
            if vals:
                features[f"battedball_{m}_L10"] = float(sum(vals) / len(vals))

        return features
