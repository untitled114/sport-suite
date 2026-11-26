"""
Statcast Rolling Feature Extractor (MLB)
========================================
Extracts rolling Statcast skill metrics (Baseball Savant) for a player as of a
game date. These advanced metrics - whiff/CSW/putaway for pitchers, exit
velocity / barrel / xwOBA for batters - are the underlying skill signals that
drive stat outcomes.

Routing by stat_type:
    STRIKEOUTS              -> pitcher metrics (statcast_pitcher_games)
    HITS / TOTAL_BASES      -> batter metrics  (statcast_batter_games)

Rolling windows: L10, L30, and season-to-date. All windows are built from games
STRICTLY BEFORE ``game_date`` (``game_date < %s``) so there is no leakage of the
target game's own outcome into its features.

Mirrors the BaseFeatureExtractor interface (``extract`` / ``get_defaults`` /
``FEATURE_NAMES``) used by the other ``mlb/features/extractors`` modules.
Source tables are upserted by ``mlb.scripts.loaders.load_statcast``.
"""

import logging
from typing import Any, Dict, List

import numpy as np
import pandas as pd

from .base import BaseFeatureExtractor

logger = logging.getLogger(__name__)

PITCHER_STATS = frozenset({"STRIKEOUTS"})
BATTER_STATS = frozenset({"HITS", "TOTAL_BASES"})


class StatcastFeatureExtractor(BaseFeatureExtractor):
    """Rolling Statcast (Savant) features, routed by stat type."""

    WINDOWS = (10, 30)  # plus season-to-date

    # Pitcher metric columns (statcast_pitcher_games) used as rolling targets.
    _PITCHER_METRICS = (
        "swstr_pct",
        "csw_pct",
        "whiff_pct",
        "chase_pct",
        "putaway_pct",
        "avg_fastball_velo",
    )
    # Batter metric columns (statcast_batter_games) used as rolling targets.
    _BATTER_METRICS = (
        "avg_exit_velo",
        "hard_hit_pct",
        "barrel_pct",
        "sweet_spot_pct",
        "xba",
        "xwoba",
        "xslg",
        "whiff_pct",
        "chase_pct",
    )

    # League-ish neutral defaults (used when a player has no Statcast history).
    _PITCHER_DEFAULTS = {
        "swstr_pct": 0.11,
        "csw_pct": 0.28,
        "whiff_pct": 0.24,
        "chase_pct": 0.28,
        "putaway_pct": 0.19,
        "avg_fastball_velo": 93.5,
    }
    _BATTER_DEFAULTS = {
        "avg_exit_velo": 88.5,
        "hard_hit_pct": 0.38,
        "barrel_pct": 0.07,
        "sweet_spot_pct": 0.33,
        "xba": 0.245,
        "xwoba": 0.315,
        "xslg": 0.400,
        "whiff_pct": 0.24,
        "chase_pct": 0.28,
    }

    FEATURE_NAMES = (
        # Pitcher rolling (L10 / L30 / season)
        "statcast_pitcher_swstr_pct_L10",
        "statcast_pitcher_swstr_pct_L30",
        "statcast_pitcher_swstr_pct_season",
        "statcast_pitcher_csw_pct_L10",
        "statcast_pitcher_csw_pct_L30",
        "statcast_pitcher_csw_pct_season",
        "statcast_pitcher_whiff_pct_L10",
        "statcast_pitcher_whiff_pct_L30",
        "statcast_pitcher_whiff_pct_season",
        "statcast_pitcher_chase_pct_L10",
        "statcast_pitcher_chase_pct_L30",
        "statcast_pitcher_putaway_pct_L10",
        "statcast_pitcher_putaway_pct_L30",
        "statcast_pitcher_avg_fastball_velo_L10",
        "statcast_pitcher_avg_fastball_velo_L30",
        "statcast_pitcher_games_sample",
        # Batter rolling (L10 / L30 / season)
        "statcast_batter_avg_exit_velo_L10",
        "statcast_batter_avg_exit_velo_L30",
        "statcast_batter_avg_exit_velo_season",
        "statcast_batter_hard_hit_pct_L10",
        "statcast_batter_hard_hit_pct_L30",
        "statcast_batter_hard_hit_pct_season",
        "statcast_batter_barrel_pct_L10",
        "statcast_batter_barrel_pct_L30",
        "statcast_batter_barrel_pct_season",
        "statcast_batter_sweet_spot_pct_L10",
        "statcast_batter_sweet_spot_pct_L30",
        "statcast_batter_xba_L10",
        "statcast_batter_xba_L30",
        "statcast_batter_xwoba_L10",
        "statcast_batter_xwoba_L30",
        "statcast_batter_xwoba_season",
        "statcast_batter_xslg_L10",
        "statcast_batter_xslg_L30",
        "statcast_batter_whiff_pct_L10",
        "statcast_batter_whiff_pct_L30",
        "statcast_batter_chase_pct_L10",
        "statcast_batter_chase_pct_L30",
        "statcast_batter_games_sample",
    )

    def __init__(self, conn: Any):
        """
        Args:
            conn: Connection to the ``mlb_players`` schema (statcast_*_games).
        """
        super().__init__(conn, name="StatcastFeatures")

    @classmethod
    def get_defaults(cls) -> Dict[str, float]:
        d: Dict[str, float] = {}
        # Pitcher
        for m in ("swstr_pct", "csw_pct", "whiff_pct"):
            for w in ("L10", "L30", "season"):
                d[f"statcast_pitcher_{m}_{w}"] = cls._PITCHER_DEFAULTS[m]
        for m in ("chase_pct", "putaway_pct", "avg_fastball_velo"):
            for w in ("L10", "L30"):
                d[f"statcast_pitcher_{m}_{w}"] = cls._PITCHER_DEFAULTS[m]
        d["statcast_pitcher_games_sample"] = 0.0
        # Batter
        for m in ("avg_exit_velo", "hard_hit_pct", "barrel_pct"):
            for w in ("L10", "L30", "season"):
                d[f"statcast_batter_{m}_{w}"] = cls._BATTER_DEFAULTS[m]
        for w in ("L10", "L30"):
            d[f"statcast_batter_sweet_spot_pct_{w}"] = cls._BATTER_DEFAULTS["sweet_spot_pct"]
            d[f"statcast_batter_xba_{w}"] = cls._BATTER_DEFAULTS["xba"]
            d[f"statcast_batter_xslg_{w}"] = cls._BATTER_DEFAULTS["xslg"]
            d[f"statcast_batter_whiff_pct_{w}"] = cls._BATTER_DEFAULTS["whiff_pct"]
            d[f"statcast_batter_chase_pct_{w}"] = cls._BATTER_DEFAULTS["chase_pct"]
        for w in ("L10", "L30", "season"):
            d[f"statcast_batter_xwoba_{w}"] = cls._BATTER_DEFAULTS["xwoba"]
        d["statcast_batter_games_sample"] = 0.0
        return d

    def extract(
        self,
        player_name: str,
        game_date: Any,
        stat_type: str = "STRIKEOUTS",
        **kwargs,
    ) -> Dict[str, float]:
        """
        Extract rolling Statcast features for a player as of ``game_date``.

        Args:
            player_name: Player full name (pitcher or batter).
            game_date: Date of the upcoming game (leakage cutoff, exclusive).
            stat_type: STRIKEOUTS -> pitcher metrics; HITS/TOTAL_BASES -> batter.
            **kwargs: ``player_id`` may be passed to skip the name lookup
                      (direct, since Statcast ids == our player_id).

        Returns:
            Dict of rolling Statcast features (defaults when no history).
        """
        game_date_str = self._normalize_date(game_date)
        stat_u = (stat_type or "").upper()
        is_pitcher = stat_u in PITCHER_STATS

        table = "statcast_pitcher_games" if is_pitcher else "statcast_batter_games"
        profile = "pitcher_profile" if is_pitcher else "batter_profile"
        metrics = self._PITCHER_METRICS if is_pitcher else self._BATTER_METRICS
        cols = ", ".join(f"sg.{m}" for m in metrics)

        player_id = kwargs.get("player_id")
        if player_id is not None:
            query = f"""
                SELECT sg.game_date, {cols}
                FROM {table} sg
                WHERE sg.player_id = %s AND sg.game_date < %s
                ORDER BY sg.game_date DESC
                LIMIT 40
            """
            params: tuple = (player_id, game_date_str)
        else:
            query = f"""
                SELECT sg.game_date, {cols}
                FROM {table} sg
                JOIN {profile} pr ON sg.player_id = pr.player_id
                WHERE unaccent(pr.full_name) = unaccent(%s)
                  AND sg.game_date < %s
                ORDER BY sg.game_date DESC
                LIMIT 40
            """
            params = (player_name, game_date_str)

        df = self._safe_query(query, params)
        if df is None or len(df) == 0:
            return self.get_defaults()

        return self._compute_features(df, is_pitcher, game_date_str)

    def extract_from_games(
        self, games: List[Dict], game_date: Any, stat_type: str = "STRIKEOUTS"
    ) -> Dict[str, float]:
        """
        Extract from pre-fetched per-game metric dicts (no query: the caller supplies the rows).

        Args:
            games: List of per-game metric dicts (most-recent-first), each with a
                   ``game_date`` plus the relevant Statcast metric columns.
            game_date: Upcoming game date.
            stat_type: Selects pitcher vs batter metric set.

        Returns:
            Dict of rolling Statcast features.
        """
        if not games:
            return self.get_defaults()
        is_pitcher = (stat_type or "").upper() in PITCHER_STATS
        df = pd.DataFrame(games)
        return self._compute_features(df, is_pitcher, self._normalize_date(game_date))

    # ------------------------------------------------------------------

    def _compute_features(
        self, df: pd.DataFrame, is_pitcher: bool, game_date_str: str
    ) -> Dict[str, float]:
        """Compute rolling means (L10/L30/season) from a game-row DataFrame (DESC)."""
        features = self.get_defaults()
        n = len(df)
        prefix = "pitcher" if is_pitcher else "batter"
        features[f"statcast_{prefix}_games_sample"] = float(n)

        metrics = self._PITCHER_METRICS if is_pitcher else self._BATTER_METRICS
        for m in metrics:
            if m not in df.columns:
                continue
            series = pd.to_numeric(df[m], errors="coerce")
            for feat_name, window in self._windows_for(prefix, m):
                if window is None:  # season-to-date == all available rows
                    vals = series.dropna()
                else:
                    vals = series.iloc[:window].dropna()
                if len(vals) > 0:
                    features[feat_name] = float(np.mean(vals))

        return features

    def _windows_for(self, prefix: str, metric: str):
        """Yield (feature_name, window_int_or_None) pairs that exist for a metric."""
        defaults = self.get_defaults()
        for w_label, w_int in (("L10", 10), ("L30", 30), ("season", None)):
            name = f"statcast_{prefix}_{metric}_{w_label}"
            if name in defaults:
                yield name, w_int
