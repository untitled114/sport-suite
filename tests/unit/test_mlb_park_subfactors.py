"""
Unit tests for the park SUB-FACTOR helpers in mlb.config.park_factors.

Covers the HR factor, hits factor, altitude lookups, the bundled
``get_park_sub_factors``, sample-size gating, and fallbacks. All factors come
from the COMPUTED_PARK_FACTORS snapshot; these tests verify lookup
plumbing, normalization, the *_known flags, and fallback behavior against a
controlled snapshot.
"""

import pytest

from mlb.config.park_factors import (
    COMPUTED_PARK_FACTORS,
    NEUTRAL_ALTITUDE_FEET,
    NEUTRAL_FACTOR,
    get_park_altitude,
    get_park_hits_factor,
    get_park_hr_factor,
    get_park_sub_factors,
)


@pytest.fixture
def snapshot():
    saved = dict(COMPUTED_PARK_FACTORS)
    COMPUTED_PARK_FACTORS.clear()
    COMPUTED_PARK_FACTORS.update(
        {
            "COL": (1.24, 1.15, 1.23, 239),
            "SDP": (0.92, 0.98, 0.94, 247),
            "NYY": (1.02, 1.16, 0.90, 253),
            "THIN": (1.50, 1.50, 1.50, 5),  # below MIN_GAMES
        }
    )
    yield
    COMPUTED_PARK_FACTORS.clear()
    COMPUTED_PARK_FACTORS.update(saved)


# --------------------------------------------------------------------------- #
# HR factor
# --------------------------------------------------------------------------- #
def test_hr_factor_known_team(snapshot):
    assert get_park_hr_factor("NYY") == 1.16


def test_hr_factor_normalizes_alias(snapshot):
    # "SD" -> canonical "SDP".
    assert get_park_hr_factor("SD") == 0.98


def test_hr_factor_unknown_team_neutral(snapshot):
    assert get_park_hr_factor("ZZZ") == NEUTRAL_FACTOR
    assert get_park_hr_factor("") == NEUTRAL_FACTOR
    assert get_park_hr_factor(None) == NEUTRAL_FACTOR


def test_hr_factor_thin_sample_neutral(snapshot):
    assert get_park_hr_factor("THIN") == NEUTRAL_FACTOR


# --------------------------------------------------------------------------- #
# Hits factor
# --------------------------------------------------------------------------- #
def test_hits_factor_known_team(snapshot):
    assert get_park_hits_factor("COL") == 1.23


def test_hits_factor_unknown_neutral(snapshot):
    assert get_park_hits_factor("ZZZ") == NEUTRAL_FACTOR
    assert get_park_hits_factor(None) == NEUTRAL_FACTOR


def test_hits_factor_thin_sample_neutral(snapshot):
    assert get_park_hits_factor("THIN") == NEUTRAL_FACTOR


# --------------------------------------------------------------------------- #
# Altitude (physical constant)
# --------------------------------------------------------------------------- #
def test_altitude_coors_extreme():
    assert get_park_altitude("COL") == 5200


def test_altitude_sea_level_park():
    assert get_park_altitude("MIA") == 8


def test_altitude_unknown_defaults_to_neutral():
    assert get_park_altitude("ZZZ") == NEUTRAL_ALTITUDE_FEET
    assert get_park_altitude(None) == NEUTRAL_ALTITUDE_FEET


# --------------------------------------------------------------------------- #
# Bundle
# --------------------------------------------------------------------------- #
def test_sub_factors_bundle_coors(snapshot):
    feats = get_park_sub_factors("COL")
    assert feats["park_runs_factor"] == 1.24
    assert feats["park_hr_factor"] == 1.15
    assert feats["park_hits_factor"] == 1.23
    assert feats["park_altitude_feet"] == 5200.0
    assert feats["park_is_coors"] == 1.0
    assert feats["park_factors_known"] == 1.0
    assert feats["park_batter_hand_known"] == 0.0


def test_sub_factors_bundle_non_coors_with_hand(snapshot):
    feats = get_park_sub_factors("SDP", batter_hand="L")
    assert feats["park_is_coors"] == 0.0
    assert feats["park_batter_hand_known"] == 1.0
    assert feats["park_hr_factor"] == get_park_hr_factor("SDP")


def test_sub_factors_bundle_invalid_hand_not_known(snapshot):
    feats = get_park_sub_factors("NYY", batter_hand="X")
    assert feats["park_batter_hand_known"] == 0.0


def test_sub_factors_bundle_switch_hand_known(snapshot):
    feats = get_park_sub_factors("NYY", batter_hand="s")
    assert feats["park_batter_hand_known"] == 1.0


def test_sub_factors_bundle_unknown_park_neutral(snapshot):
    feats = get_park_sub_factors("ZZZ")
    assert feats["park_runs_factor"] == NEUTRAL_FACTOR
    assert feats["park_hr_factor"] == NEUTRAL_FACTOR
    assert feats["park_hits_factor"] == NEUTRAL_FACTOR
    assert feats["park_altitude_feet"] == float(NEUTRAL_ALTITUDE_FEET)
    assert feats["park_is_coors"] == 0.0
    assert feats["park_factors_known"] == 0.0


def test_sub_factors_bundle_thin_sample_not_known(snapshot):
    feats = get_park_sub_factors("THIN")
    assert feats["park_factors_known"] == 0.0
    assert feats["park_hr_factor"] == NEUTRAL_FACTOR
