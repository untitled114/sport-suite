"""
Pitcher Rolling Feature Extractor (MLB)
=======================================
Extracts rolling pitcher performance features used by the STRIKEOUTS stat type.

Mirrors the NBA rolling-stat idiom (L5/L10/L15 EMAs + trend ratios + rest)
adapted for baseball pitchers. Source: ``mlb_players.pitcher_game_logs``.

Feature groups:
1. Rolling K production (EMAs of strikeouts over L5/L10/L15)
2. Rolling rate stats (K/9, K per batter faced, K per pitch)
3. Workload (innings pitched, batters faced, pitch count averages)
4. Form / trend (L5 vs L15 trend ratios, K std volatility)
5. Recency context (days rest, last-start strikeouts/IP)
"""

import logging
from typing import Any, Dict, List

import numpy as np
import pandas as pd

from mlb.sql import named_query

from .base import BaseFeatureExtractor

logger = logging.getLogger(__name__)


def _ema(values: List[float], alpha: float = 0.4) -> float:
    """Exponential moving average over a list (most-recent-first ordering)."""
    valid = [float(v) for v in values if v is not None and not pd.isna(v)]
    if not valid:
        return 0.0
    ema = valid[0]
    for v in valid[1:]:
        ema = alpha * v + (1 - alpha) * ema
    return ema


class PitcherFeatureExtractor(BaseFeatureExtractor):
    """Rolling pitcher features for the STRIKEOUTS stat type."""

    WINDOWS = (5, 10, 15)

    FEATURE_NAMES = (
        # Rolling strikeout production
        "pitcher_ema_strikeouts_L5",
        "pitcher_ema_strikeouts_L10",
        "pitcher_ema_strikeouts_L15",
        # Rolling rate stats
        "pitcher_k_per_9_L10",
        "pitcher_k_per_bf_L10",
        "pitcher_k_per_pitch_L10",
        # Workload
        "pitcher_ema_innings_L5",
        "pitcher_ema_innings_L10",
        "pitcher_ema_batters_faced_L10",
        "pitcher_ema_pitches_L10",
        # Form / volatility
        "pitcher_strikeouts_std_L10",
        "pitcher_strikeouts_trend_ratio",
        "pitcher_innings_trend_ratio",
        # Recency context
        "pitcher_days_rest",
        "pitcher_last_start_strikeouts",
        "pitcher_last_start_innings",
        "pitcher_games_sample",
    )

    def __init__(self, conn: Any):
        """
        Args:
            conn: Connection to the ``mlb_players`` schema (pitcher_game_logs).
        """
        super().__init__(conn, name="PitcherFeatures")

    @classmethod
    def get_defaults(cls) -> Dict[str, float]:
        return {
            "pitcher_ema_strikeouts_L5": 5.0,
            "pitcher_ema_strikeouts_L10": 5.0,
            "pitcher_ema_strikeouts_L15": 5.0,
            "pitcher_k_per_9_L10": 8.0,
            "pitcher_k_per_bf_L10": 0.22,
            "pitcher_k_per_pitch_L10": 0.05,
            "pitcher_ema_innings_L5": 5.0,
            "pitcher_ema_innings_L10": 5.0,
            "pitcher_ema_batters_faced_L10": 22.0,
            "pitcher_ema_pitches_L10": 90.0,
            "pitcher_strikeouts_std_L10": 0.0,
            "pitcher_strikeouts_trend_ratio": 1.0,
            "pitcher_innings_trend_ratio": 1.0,
            "pitcher_days_rest": 5.0,
            "pitcher_last_start_strikeouts": 5.0,
            "pitcher_last_start_innings": 5.0,
            "pitcher_games_sample": 0.0,
        }

    def extract(
        self,
        player_name: str,
        game_date: Any,
        stat_type: str = "STRIKEOUTS",
        **kwargs,
    ) -> Dict[str, float]:
        """
        Extract rolling pitcher features.

        Args:
            player_name: Pitcher full name
            game_date: Date of the upcoming start
            stat_type: STRIKEOUTS (interface consistency)

        Returns:
            Dict of pitcher rolling features
        """
        game_date_str = self._normalize_date(game_date)

        query = named_query("pitcher_last_30_games")
        df = self._safe_query(query, (player_name, game_date_str))

        if df is None or len(df) == 0:
            return self.get_defaults()

        return self._compute_features(df, game_date_str)

    def extract_from_games(self, games: List[Dict], game_date: Any) -> Dict[str, float]:
        """
        Extract features from pre-fetched game dicts (no query: the caller supplies the rows).

        Args:
            games: List of game dicts (most-recent-first) with keys
                   innings_pitched, strikeouts, batters_faced, pitches, game_date.
            game_date: Date of the upcoming start.

        Returns:
            Dict of pitcher rolling features
        """
        if not games:
            return self.get_defaults()
        df = pd.DataFrame(games)
        return self._compute_features(df, self._normalize_date(game_date))

    def _compute_features(self, df: pd.DataFrame, game_date_str: str) -> Dict[str, float]:
        """Compute rolling features from a game-log DataFrame (date DESC)."""
        features = self.get_defaults()
        features["pitcher_games_sample"] = float(len(df))

        ks = df["strikeouts"].fillna(0).astype(float).tolist()
        ip = df["innings_pitched"].fillna(0).astype(float).tolist()
        bf = df["batters_faced"].fillna(0).astype(float).tolist()
        pitches = df["pitches"].fillna(0).astype(float).tolist()

        # Rolling strikeout EMAs
        features["pitcher_ema_strikeouts_L5"] = _ema(ks[:5])
        features["pitcher_ema_strikeouts_L10"] = _ema(ks[:10])
        features["pitcher_ema_strikeouts_L15"] = _ema(ks[:15])

        # Rolling EMAs of workload
        features["pitcher_ema_innings_L5"] = _ema(ip[:5])
        features["pitcher_ema_innings_L10"] = _ema(ip[:10])
        features["pitcher_ema_batters_faced_L10"] = _ema(bf[:10])
        features["pitcher_ema_pitches_L10"] = _ema(pitches[:10])

        # Rate stats over L10 totals (avoid per-game-ratio noise)
        l10_ks = sum(ks[:10])
        l10_ip = sum(ip[:10])
        l10_bf = sum(bf[:10])
        l10_pitches = sum(pitches[:10])
        features["pitcher_k_per_9_L10"] = (l10_ks / l10_ip * 9.0) if l10_ip > 0 else 8.0
        features["pitcher_k_per_bf_L10"] = (l10_ks / l10_bf) if l10_bf > 0 else 0.22
        features["pitcher_k_per_pitch_L10"] = (l10_ks / l10_pitches) if l10_pitches > 0 else 0.05

        # Volatility
        if len(ks) >= 3:
            features["pitcher_strikeouts_std_L10"] = float(np.std(ks[:10]))

        # Trend ratios: L5 EMA vs L15 EMA
        ema_k_l5 = features["pitcher_ema_strikeouts_L5"]
        ema_k_l15 = features["pitcher_ema_strikeouts_L15"]
        features["pitcher_strikeouts_trend_ratio"] = ema_k_l5 / ema_k_l15 if ema_k_l15 > 0 else 1.0
        ema_ip_l5 = features["pitcher_ema_innings_L5"]
        ema_ip_l10 = features["pitcher_ema_innings_L10"]
        features["pitcher_innings_trend_ratio"] = ema_ip_l5 / ema_ip_l10 if ema_ip_l10 > 0 else 1.0

        # Recency context
        last = df.iloc[0]
        features["pitcher_last_start_strikeouts"] = self._safe_float(last["strikeouts"], 5.0)
        features["pitcher_last_start_innings"] = self._safe_float(last["innings_pitched"], 5.0)

        last_date = self._to_date(last["game_date"])
        cur_date = self._to_date(game_date_str)
        if last_date is not None and cur_date is not None:
            days_rest = (cur_date - last_date).days
            features["pitcher_days_rest"] = float(max(0, min(days_rest, 14)))

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
