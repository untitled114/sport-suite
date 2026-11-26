"""
Temporal Feature Extractor (MLB)
================================
Extracts calendar/season-phase features from the game date alone, plus
optional player-level rest/streak context. No external API needed.

The MLB season runs ~late March/April through October (regular season),
with the postseason in October. Phases capture structural breaks that affect
performance in ways rolling stats miss:
  - Early (Mar/Apr): rotations/rust, small samples
  - Mid (May-Aug): stable
  - Late (Sep): playoff push, September call-ups, rest management
  - Postseason (Oct): peak intensity

Pitchers start on rotation (~every 5 days); batters play near-daily, so days
rest is mostly meaningful for pitchers but encoded for both.
"""

import logging
from datetime import date, datetime
from typing import Any, Dict

import pandas as pd

from mlb.utils.season_helpers import MLB_SEASON_BOUNDS

from .base import BaseFeatureExtractor

logger = logging.getLogger(__name__)

# Season boundaries live in mlb.utils.season_helpers (single source of truth).
DEFAULT_SEASON_DAYS = 185  # ~Opening Day to end of regular season

# All-Star Game date per season (the single mid-July break day, derived from the
# schedule gap - the day with no regular-season games). The break is a real
# structural signal: first-half vs second-half performance shifts, rotation/usage
# resets, and accumulated fatigue into the break.
ALL_STAR_GAME = {
    2024: "2024-07-16",
    2025: "2025-07-15",
    2026: "2026-07-14",
}


def _parse_date(value: Any) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()


class TemporalFeatureExtractor(BaseFeatureExtractor):
    """Season-phase + rest/trend features (calendar-driven, optional player rest)."""

    FEATURE_NAMES = (
        "season_phase_encoded",  # 1=early, 2=mid, 3=late, 4=postseason
        "is_early_season",
        "is_mid_season",
        "is_late_season",
        "is_postseason",
        "season_pct",
        "month_of_season",
        "day_of_week",
        "days_rest",
        "is_short_rest",
        "is_second_half",  # post All-Star break
        "days_from_all_star",  # signed: <0 before break, >0 after
    )

    def __init__(self, conn: Any = None):
        """
        Args:
            conn: Optional connection (unused for calendar features; accepted for
                  interface parity). Player rest is passed via kwargs.
        """
        super().__init__(conn, name="Temporal")

    @classmethod
    def get_defaults(cls) -> Dict[str, float]:
        return {
            "season_phase_encoded": 2.0,
            "is_early_season": 0.0,
            "is_mid_season": 1.0,
            "is_late_season": 0.0,
            "is_postseason": 0.0,
            "season_pct": 0.5,
            "month_of_season": 6.0,
            "day_of_week": 2.0,
            "days_rest": 4.0,
            "is_short_rest": 0.0,
            "is_second_half": 0.0,
            "days_from_all_star": 0.0,
        }

    def extract(
        self,
        player_name: str,
        game_date: Any,
        stat_type: str = "HITS",
        days_rest: float = None,
        **kwargs,
    ) -> Dict[str, float]:
        """
        Extract temporal features.

        Args:
            player_name: Player full name (unused; interface consistency)
            game_date: Game date
            stat_type: Stat type (interface consistency)
            days_rest: Optional days of rest since last appearance (pitcher days
                       between starts / batter days since last game).

        Returns:
            Dict of temporal features
        """
        features = self.get_defaults()
        gd = _parse_date(game_date)
        self._compute_phase(gd, features)

        features["day_of_week"] = float(gd.weekday())  # Mon=0 .. Sun=6

        if days_rest is not None and not pd.isna(days_rest):
            rest = float(max(0, min(days_rest, 14)))
            features["days_rest"] = rest
            features["is_short_rest"] = 1.0 if rest <= 3 else 0.0

        return self.validate_features(features)

    def _compute_phase(self, gd: date, features: Dict[str, float]) -> None:
        """Compute season phase + progress from the game date."""
        month = gd.month
        features["month_of_season"] = float(month)

        bounds = MLB_SEASON_BOUNDS.get(gd.year)
        if bounds:
            start = _parse_date(bounds["start"])
            regular_end = _parse_date(bounds["regular_end"])
            postseason_start = _parse_date(bounds["postseason_start"])
        else:
            start = date(gd.year, 3, 28)
            regular_end = date(gd.year, 9, 28)
            postseason_start = date(gd.year, 9, 30)

        total_days = (regular_end - start).days or DEFAULT_SEASON_DAYS
        days_in = (gd - start).days
        features["season_pct"] = float(max(0.0, min(1.0, days_in / total_days)))

        # Phase classification
        if gd >= postseason_start:
            phase = 4
        elif month in (3, 4):
            phase = 1
        elif month in (5, 6, 7, 8):
            phase = 2
        else:  # September
            phase = 3

        features["season_phase_encoded"] = float(phase)
        features["is_early_season"] = 1.0 if phase == 1 else 0.0
        features["is_mid_season"] = 1.0 if phase == 2 else 0.0
        features["is_late_season"] = 1.0 if phase == 3 else 0.0
        features["is_postseason"] = 1.0 if phase == 4 else 0.0

        # All-Star break: first-half vs second-half split + signed distance.
        asg_str = ALL_STAR_GAME.get(gd.year)
        asg = _parse_date(asg_str) if asg_str else date(gd.year, 7, 15)
        features["days_from_all_star"] = float((gd - asg).days)
        features["is_second_half"] = 1.0 if gd > asg else 0.0
