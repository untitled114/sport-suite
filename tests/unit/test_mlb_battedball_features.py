"""
Unit tests for mlb.features.extractors.battedball_features.BattedBallFeatureExtractor.

The DB is mocked: ``_safe_query`` returns hand-built per-game batted-ball
DataFrames (date DESC). Tests assert L10 rolling math, leakage cutoff wiring,
id-vs-name routing, and defaults.
"""

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from mlb.features.extractors.battedball_features import BattedBallFeatureExtractor


@pytest.fixture
def extractor():
    return BattedBallFeatureExtractor(conn=MagicMock())


def _df(rows=2):
    base = {
        "game_date": ["2024-06-30", "2024-06-25"][:rows],
        "gb_rate": [0.40, 0.50][:rows],
        "fb_rate": [0.30, 0.40][:rows],
        "ld_rate": [0.20, 0.30][:rows],
        "popup_rate": [0.05, 0.15][:rows],
        "pull_rate": [0.40, 0.50][:rows],
    }
    return pd.DataFrame(base)


def test_feature_names_in_defaults():
    defaults = BattedBallFeatureExtractor.get_defaults()
    for name in BattedBallFeatureExtractor.FEATURE_NAMES:
        assert name in defaults


def test_no_history_returns_defaults(extractor):
    with patch.object(extractor, "_safe_query", return_value=None):
        feats = extractor.extract("Some Batter", "2024-07-01", stat_type="HITS")
    assert feats == BattedBallFeatureExtractor.get_defaults()
    assert feats["battedball_games_sample"] == 0.0


def test_name_join_query_and_rolling(extractor):
    captured = {}

    def fake_query(query, params, conn=None):
        captured["query"] = query
        captured["params"] = params
        return _df(2)

    with patch.object(extractor, "_safe_query", side_effect=fake_query):
        feats = extractor.extract("Slugger", "2024-07-01", stat_type="TOTAL_BASES")

    assert "statcast_batter_battedball_games" in captured["query"]
    assert "bb.game_date < %s" in captured["query"]
    assert "JOIN batter_profile" in captured["query"]
    assert captured["params"] == ("Slugger", "2024-07-01")
    assert feats["battedball_gb_rate_L10"] == pytest.approx(0.45)
    assert feats["battedball_pull_rate_L10"] == pytest.approx(0.45)
    assert feats["battedball_games_sample"] == 2.0


def test_player_id_skips_name_join(extractor):
    captured = {}

    def fake_query(query, params, conn=None):
        captured["query"] = query
        captured["params"] = params
        return _df(1)

    with patch.object(extractor, "_safe_query", side_effect=fake_query):
        extractor.extract("Slugger", "2024-07-01", player_id=999)

    assert "JOIN" not in captured["query"]
    assert "bb.player_id = %s" in captured["query"]
    assert captured["params"] == (999, "2024-07-01")


def test_l10_window_caps_at_10(extractor):
    rates = [0.30 + i * 0.01 for i in range(12)]
    df = pd.DataFrame(
        {
            "game_date": [f"2024-06-{30 - i:02d}" for i in range(12)],
            "gb_rate": rates,
        }
    )
    with patch.object(extractor, "_safe_query", return_value=df):
        feats = extractor.extract("Slugger", "2024-07-01")
    assert feats["battedball_gb_rate_L10"] == pytest.approx(sum(rates[:10]) / 10)
    assert feats["battedball_games_sample"] == 12.0


def test_extract_from_games(extractor):
    games = [{"game_date": "2024-06-30", "ld_rate": 0.25}]
    feats = extractor.extract_from_games(games, "2024-07-01")
    assert feats["battedball_ld_rate_L10"] == pytest.approx(0.25)
    assert feats["battedball_games_sample"] == 1.0


def test_extract_from_games_empty(extractor):
    assert (
        extractor.extract_from_games([], "2024-07-01") == BattedBallFeatureExtractor.get_defaults()
    )


def test_missing_metric_column_skipped(extractor):
    games = [{"game_date": "2024-06-30", "gb_rate": 0.55}]
    defaults = BattedBallFeatureExtractor.get_defaults()
    feats = extractor.extract_from_games(games, "2024-07-01")
    assert feats["battedball_gb_rate_L10"] == pytest.approx(0.55)
    assert feats["battedball_fb_rate_L10"] == pytest.approx(defaults["battedball_fb_rate_L10"])


def test_nan_metric_falls_back_to_default(extractor):
    df = pd.DataFrame({"game_date": ["2024-06-30"], "pull_rate": [float("nan")]})
    defaults = BattedBallFeatureExtractor.get_defaults()
    with patch.object(extractor, "_safe_query", return_value=df):
        feats = extractor.extract("Slugger", "2024-07-01")
    assert feats["battedball_pull_rate_L10"] == pytest.approx(defaults["battedball_pull_rate_L10"])
    assert feats["battedball_games_sample"] == 1.0
