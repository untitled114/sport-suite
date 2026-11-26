#!/usr/bin/env python3
"""
Context Modifier - Rule-Based Projection Adjustments
====================================================
Applies empirically-derived adjustments to a stat projection based on
game context (home/away, back-to-back, blowout risk, rest days).

Coefficients derived from linear regression of (actual / season_avg) on
43,998 game logs (2023-2026, minutes >= 20). The effects are small: context explains very
little of the game-to-game variation, so the multiplier is clamped to 0.90 .. 1.10.

The modifier is multiplicative:
    adjusted_projection = projection * context_multiplier

Usage:
    from nba.models.context_modifier import ContextModifier
    modifier = ContextModifier()
    adjusted = modifier.adjust(projection=25.3, stat_type="POINTS", context={
        "is_home": True,
        "is_back_to_back": False,
        "days_rest": 2,
        "expected_margin": -4.0,
    })
"""

import logging
from dataclasses import dataclass
from typing import Any

log = logging.getLogger("nba.context_modifier")


@dataclass(frozen=True)
class ContextCoefficients:
    """Empirically derived from 43,998 game logs (2023-2026).

    Each coefficient represents the multiplicative effect on actual/season_avg.
    E.g., home=+0.014 means home players score ~1.4% more than their average.
    """

    # POINTS coefficients
    points_home: float = 0.0140
    points_b2b: float = 0.0047
    points_rest_3plus: float = -0.0099
    points_margin_per_pt: float = -0.0002
    points_blowout: float = 0.0007

    # REBOUNDS coefficients
    rebounds_home: float = 0.0070
    rebounds_b2b: float = -0.0100
    rebounds_rest_3plus: float = -0.0089
    rebounds_margin_per_pt: float = 0.0
    rebounds_blowout: float = -0.0083


CONTEXT_COEFFICIENTS = ContextCoefficients()


class ContextModifier:
    """Apply context-based adjustments to a stat projection.

    The effects are small, and every multiplier is clamped to [0.90, 1.10].
    """

    def __init__(self, coefficients: ContextCoefficients = CONTEXT_COEFFICIENTS):
        self.coeff = coefficients

    def compute_multiplier(self, stat_type: str, context: dict[str, Any]) -> float:
        """Compute the context multiplier for a given game context.

        Args:
            stat_type: "POINTS" or "REBOUNDS"
            context: Dict with keys:
                is_home (bool), is_back_to_back (bool),
                days_rest (int), expected_margin (float: the team's projected point margin for the game)

        Returns:
            Multiplier in [0.90, 1.10] range.
        """
        c = self.coeff
        prefix = stat_type.lower()

        home = getattr(c, f"{prefix}_home", 0)
        b2b = getattr(c, f"{prefix}_b2b", 0)
        rest_3 = getattr(c, f"{prefix}_rest_3plus", 0)
        margin_pt = getattr(c, f"{prefix}_margin_per_pt", 0)
        blowout = getattr(c, f"{prefix}_blowout", 0)

        multiplier = 1.0

        # Home/away
        is_home = context.get("is_home", False)
        if is_home:
            multiplier += home
        else:
            multiplier -= home

        # Back-to-back
        if context.get("is_back_to_back", False):
            multiplier += b2b

        # Rest advantage (3+ days)
        days_rest = context.get("days_rest", 2)
        if days_rest is not None and days_rest >= 3:
            multiplier += rest_3

        # Margin effect (continuous)
        margin = context.get("expected_margin")
        if margin is not None:
            multiplier += margin_pt * margin

        # Blowout risk (|margin| > 10)
        if margin is not None and abs(margin) > 10:
            multiplier += blowout

        # Clamp to prevent overcorrection
        multiplier = max(0.90, min(1.10, multiplier))
        return round(multiplier, 4)

    def adjust(
        self,
        projection: float,
        stat_type: str,
        context: dict[str, Any],
    ) -> float:
        """Apply context adjustment to a projection.

        Args:
            projection: The raw projection (e.g., 25.3 points)
            stat_type: "POINTS" or "REBOUNDS"
            context: Game context dict

        Returns:
            Adjusted projection.
        """
        multiplier = self.compute_multiplier(stat_type, context)
        adjusted = projection * multiplier
        return round(adjusted, 2)

    def adjust_batch(
        self,
        projections: list[float],
        stat_type: str,
        contexts: list[dict[str, Any]],
    ) -> list[float]:
        """Apply context adjustments to a batch of projections."""
        return [self.adjust(proj, stat_type, ctx) for proj, ctx in zip(projections, contexts)]
