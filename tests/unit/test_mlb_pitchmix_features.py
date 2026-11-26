"""
Unit tests for mlb.features.extractors.pitchmix_features.PitchMixFeatureExtractor.

The DB is mocked: ``_safe_query`` is patched to return hand-built per-game
pitch-mix DataFrames (date DESC). Tests assert the L10 rolling math, leakage
cutoff wiring (game_date < %s), id-vs-name routing, and defaults.
"""

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from mlb.features.extractors.pitchmix_features import PitchMixFeatureExtractor


@pytest.fixture
def extractor():
    return PitchMixFeatureExtractor(conn=MagicMock())


def _df(rows=2):
    base = {
        "game_date": ["2024-06-30", "2024-06-25"][:rows],
        "num_pitch_types": [4, 6][:rows],
        "fastball_velo": [95.0, 97.0][:rows],
        "fastball_spin": [2300.0, 2500.0][:rows],
        "breaking_spin": [2400.0, 2600.0][:rows],
        "zone_rate": [0.50, 0.40][:rows],
        "first_strike_rate": [0.60, 0.70][:rows],
        "fastball_whiff_rate": [0.20, 0.30][:rows],
        "slider_whiff_rate": [0.30, 0.40][:rows],
        "curve_whiff_rate": [0.25, 0.35][:rows],
        "changeup_whiff_rate": [0.30, 0.40][:rows],
    }
    return pd.DataFrame(base)


def test_feature_names_in_defaults():
    defaults = PitchMixFeatureExtractor.get_defaults()
    for name in PitchMixFeatureExtractor.FEATURE_NAMES:
        assert name in defaults


def test_no_history_returns_defaults(extractor):
    with patch.object(extractor, "_safe_query", return_value=None):
        feats = extractor.extract("Some Pitcher", "2024-07-01")
    assert feats == PitchMixFeatureExtractor.get_defaults()
    assert feats["pitchmix_games_sample"] == 0.0


def test_name_join_query_and_rolling(extractor):
    captured = {}

    def fake_query(query, params, conn=None):
        captured["query"] = query
        captured["params"] = params
        return _df(2)

    with patch.object(extractor, "_safe_query", side_effect=fake_query):
        feats = extractor.extract("Ace", "2024-07-01", stat_type="STRIKEOUTS")

    assert "statcast_pitcher_pitchmix_games" in captured["query"]
    assert "pm.game_date < %s" in captured["query"]
    assert "JOIN pitcher_profile" in captured["query"]
    assert captured["params"] == ("Ace", "2024-07-01")
    assert feats["pitchmix_fastball_velo_L10"] == pytest.approx(96.0)
    assert feats["pitchmix_slider_whiff_rate_L10"] == pytest.approx(0.35)
    assert feats["pitchmix_games_sample"] == 2.0


def test_player_id_skips_name_join(extractor):
    captured = {}

    def fake_query(query, params, conn=None):
        captured["query"] = query
        captured["params"] = params
        return _df(1)

    with patch.object(extractor, "_safe_query", side_effect=fake_query):
        extractor.extract("Ace", "2024-07-01", player_id=12345)

    assert "JOIN" not in captured["query"]
    assert "pm.player_id = %s" in captured["query"]
    assert captured["params"] == (12345, "2024-07-01")


def test_l10_window_caps_at_10(extractor):
    # 12 games: L10 = mean of first 10 fastball_velo values.
    velos = [90.0 + i for i in range(12)]
    df = pd.DataFrame(
        {
            "game_date": [f"2024-06-{30 - i:02d}" for i in range(12)],
            "fastball_velo": velos,
        }
    )
    with patch.object(extractor, "_safe_query", return_value=df):
        feats = extractor.extract("Ace", "2024-07-01")
    assert feats["pitchmix_fastball_velo_L10"] == pytest.approx(sum(velos[:10]) / 10)
    assert feats["pitchmix_games_sample"] == 12.0


def test_extract_from_games(extractor):
    games = [
        {"game_date": "2024-06-30", "fastball_velo": 95.0, "zone_rate": 0.5},
    ]
    feats = extractor.extract_from_games(games, "2024-07-01")
    assert feats["pitchmix_fastball_velo_L10"] == pytest.approx(95.0)
    assert feats["pitchmix_games_sample"] == 1.0


def test_extract_from_games_empty(extractor):
    assert extractor.extract_from_games([], "2024-07-01") == PitchMixFeatureExtractor.get_defaults()


def test_missing_metric_column_skipped(extractor):
    games = [{"game_date": "2024-06-30", "fastball_velo": 94.0}]
    defaults = PitchMixFeatureExtractor.get_defaults()
    feats = extractor.extract_from_games(games, "2024-07-01")
    assert feats["pitchmix_fastball_velo_L10"] == pytest.approx(94.0)
    # zone_rate column absent -> default retained
    assert feats["pitchmix_zone_rate_L10"] == pytest.approx(defaults["pitchmix_zone_rate_L10"])


def test_nan_metric_falls_back_to_default(extractor):
    df = pd.DataFrame({"game_date": ["2024-06-30"], "fastball_velo": [float("nan")]})
    defaults = PitchMixFeatureExtractor.get_defaults()
    with patch.object(extractor, "_safe_query", return_value=df):
        feats = extractor.extract("Ace", "2024-07-01")
    assert feats["pitchmix_fastball_velo_L10"] == pytest.approx(
        defaults["pitchmix_fastball_velo_L10"]
    )
    assert feats["pitchmix_games_sample"] == 1.0
