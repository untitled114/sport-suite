"""
Batter Rolling Feature Extractor (MLB)
======================================
Extracts rolling batter performance features used by the HITS and TOTAL_BASES
stats.

Mirrors the NBA rolling-stat idiom (L5/L10/L15 EMAs + trend ratios + rest)
adapted for baseball batters. Source: ``mlb_players.batter_game_logs``.

The target stat for the rolling EMAs/std/trend depends on the stat_type:
    HITS        -> hits
    TOTAL_BASES -> total_bases

Cross-stat EMAs (at-bats, plate appearances) provide opportunity context.
"""

import logging
from typing import Any, Dict, List

import numpy as np
import pandas as pd

from mlb.sql import named_query

from .base import BaseFeatureExtractor

logger = logging.getLogger(__name__)

# stat type -> game-log column used as the rolling target
STAT_TARGET = {
    "HITS": "hits",
    "TOTAL_BASES": "total_bases",
}


def _ema(values: List[float], alpha: float = 0.4) -> float:
    """Exponential moving average over a list (most-recent-first ordering)."""
    valid = [float(v) for v in values if v is not None and not pd.isna(v)]
    if not valid:
        return 0.0
    ema = valid[0]
    for v in valid[1:]:
        ema = alpha * v + (1 - alpha) * ema
    return ema


class BatterFeatureExtractor(BaseFeatureExtractor):
    """Rolling batter features for HITS / TOTAL_BASES stat types."""

    WINDOWS = (5, 10, 15)

    FEATURE_NAMES = (
        # Rolling target-stat production (stat-specific column names)
        "batter_ema_target_L5",
        "batter_ema_target_L10",
        "batter_ema_target_L15",
        "batter_target_std_L10",
        "batter_target_trend_ratio",
        "batter_target_per_ab_L10",
        # Cross-stat rolling production
        "batter_ema_hits_L10",
        "batter_ema_total_bases_L10",
        "batter_ema_home_runs_L15",
        "batter_ema_runs_L10",
        "batter_ema_rbis_L10",
        # Opportunity / discipline
        "batter_ema_at_bats_L10",
        "batter_ema_plate_appearances_L10",
        "batter_walk_rate_L15",
        "batter_strikeout_rate_L15",
        # Rate stats
        "batter_avg_L15",
        "batter_slg_L15",
        # Recency context
        "batter_days_rest",
        "batter_last_game_target",
        "batter_games_sample",
    )

    def __init__(self, conn: Any):
        """
        Args:
            conn: Connection to the ``mlb_players`` schema (batter_game_logs).
        """
        super().__init__(conn, name="BatterFeatures")

    @classmethod
    def get_defaults(cls) -> Dict[str, float]:
        return {
            "batter_ema_target_L5": 0.0,
            "batter_ema_target_L10": 0.0,
            "batter_ema_target_L15": 0.0,
            "batter_target_std_L10": 0.0,
            "batter_target_trend_ratio": 1.0,
            "batter_target_per_ab_L10": 0.0,
            "batter_ema_hits_L10": 1.0,
            "batter_ema_total_bases_L10": 1.5,
            "batter_ema_home_runs_L15": 0.2,
            "batter_ema_runs_L10": 0.6,
            "batter_ema_rbis_L10": 0.6,
            "batter_ema_at_bats_L10": 3.8,
            "batter_ema_plate_appearances_L10": 4.2,
            "batter_walk_rate_L15": 0.08,
            "batter_strikeout_rate_L15": 0.22,
            "batter_avg_L15": 0.250,
            "batter_slg_L15": 0.400,
            "batter_days_rest": 1.0,
            "batter_last_game_target": 0.0,
            "batter_games_sample": 0.0,
        }

    def extract(
        self,
        player_name: str,
        game_date: Any,
        stat_type: str = "HITS",
        **kwargs,
    ) -> Dict[str, float]:
        """
        Extract rolling batter features.

        Args:
            player_name: Batter full name
            game_date: Date of the upcoming game
            stat_type: HITS or TOTAL_BASES (selects rolling target stat)

        Returns:
            Dict of batter rolling features
        """
        game_date_str = self._normalize_date(game_date)

        query = named_query("batter_last_30_games")
        df = self._safe_query(query, (player_name, game_date_str))

        if df is None or len(df) == 0:
            return self.get_defaults()

        return self._compute_features(df, game_date_str, stat_type)

    def extract_from_games(
        self, games: List[Dict], game_date: Any, stat_type: str = "HITS"
    ) -> Dict[str, float]:
        """
        Extract features from pre-fetched game dicts (no query: the caller supplies the rows).

        Args:
            games: List of game dicts (most-recent-first).
            game_date: Date of the upcoming game.
            stat_type: HITS or TOTAL_BASES.

        Returns:
            Dict of batter rolling features
        """
        if not games:
            return self.get_defaults()
        df = pd.DataFrame(games)
        return self._compute_features(df, self._normalize_date(game_date), stat_type)

    def _compute_features(
        self, df: pd.DataFrame, game_date_str: str, stat_type: str
    ) -> Dict[str, float]:
        """Compute rolling features from a game-log DataFrame (date DESC)."""
        features = self.get_defaults()
        features["batter_games_sample"] = float(len(df))

        for col in (
            "at_bats",
            "hits",
            "total_bases",
            "runs",
            "rbis",
            "home_runs",
            "walks",
            "strikeouts",
            "plate_appearances",
        ):
            if col not in df.columns:
                df[col] = 0
            df[col] = df[col].fillna(0).astype(float)

        target_col = STAT_TARGET.get(stat_type.upper(), "hits")
        target = df[target_col].tolist()

        # Rolling target EMAs
        features["batter_ema_target_L5"] = _ema(target[:5])
        features["batter_ema_target_L10"] = _ema(target[:10])
        features["batter_ema_target_L15"] = _ema(target[:15])
        if len(target) >= 3:
            features["batter_target_std_L10"] = float(np.std(target[:10]))
        ema_t5 = features["batter_ema_target_L5"]
        ema_t15 = features["batter_ema_target_L15"]
        features["batter_target_trend_ratio"] = ema_t5 / ema_t15 if ema_t15 > 0 else 1.0

        # Cross-stat rolling production
        features["batter_ema_hits_L10"] = _ema(df["hits"].tolist()[:10])
        features["batter_ema_total_bases_L10"] = _ema(df["total_bases"].tolist()[:10])
        features["batter_ema_home_runs_L15"] = _ema(df["home_runs"].tolist()[:15])
        features["batter_ema_runs_L10"] = _ema(df["runs"].tolist()[:10])
        features["batter_ema_rbis_L10"] = _ema(df["rbis"].tolist()[:10])

        # Opportunity
        features["batter_ema_at_bats_L10"] = _ema(df["at_bats"].tolist()[:10])
        features["batter_ema_plate_appearances_L10"] = _ema(df["plate_appearances"].tolist()[:10])

        # L10 totals for rate stats
        l10 = df.head(10)
        l10_ab = float(l10["at_bats"].sum())
        l10_target = float(l10[target_col].sum())
        features["batter_target_per_ab_L10"] = l10_target / l10_ab if l10_ab > 0 else 0.0

        # L15 totals for discipline / slash-line rates
        l15 = df.head(15)
        l15_pa = float(l15["plate_appearances"].sum())
        l15_ab = float(l15["at_bats"].sum())
        l15_hits = float(l15["hits"].sum())
        l15_tb = float(l15["total_bases"].sum())
        l15_walks = float(l15["walks"].sum())
        l15_k = float(l15["strikeouts"].sum())

        if l15_pa > 0:
            features["batter_walk_rate_L15"] = l15_walks / l15_pa
            features["batter_strikeout_rate_L15"] = l15_k / l15_pa
        if l15_ab > 0:
            features["batter_avg_L15"] = l15_hits / l15_ab
            features["batter_slg_L15"] = l15_tb / l15_ab

        # Recency context
        last = df.iloc[0]
        features["batter_last_game_target"] = self._safe_float(last[target_col], 0.0)
        last_date = self._to_date(last["game_date"])
        cur_date = self._to_date(game_date_str)
        if last_date is not None and cur_date is not None:
            days_rest = (cur_date - last_date).days
            features["batter_days_rest"] = float(max(0, min(days_rest, 14)))

        return features

    @staticmethod
    def _to_date(value: Any):
        """Coerce a date-ish value to a date, or None."""
        if value is None:
            return None
        try:
            return pd.to_datetime(value).date()
        except (ValueError, TypeError):
            return None
