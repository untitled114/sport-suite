"""
Unit tests for mlb.features.extractors.statcast_features.StatcastFeatureExtractor.

The DB is mocked: ``_safe_query`` is patched to return hand-built per-game metric
DataFrames (date DESC). Tests assert the rolling-mean math (L10/L30/season),
stat type routing (pitcher vs batter table), leakage cutoff wiring, and defaults.
"""

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from mlb.features.extractors.statcast_features import StatcastFeatureExtractor


@pytest.fixture
def extractor():
    return StatcastFeatureExtractor(conn=MagicMock())


def test_feature_names_in_defaults(extractor):
    defaults = StatcastFeatureExtractor.get_defaults()
    for name in StatcastFeatureExtractor.FEATURE_NAMES:
        assert name in defaults


def test_no_history_returns_defaults(extractor):
    with patch.object(extractor, "_safe_query", return_value=None):
        feats = extractor.extract("Some Pitcher", "2024-07-01", stat_type="STRIKEOUTS")
    assert feats == StatcastFeatureExtractor.get_defaults()
    assert feats["statcast_pitcher_games_sample"] == 0.0


def test_pitcher_routing_uses_pitcher_table(extractor):
    captured = {}

    def fake_query(query, params, conn=None):
        captured["query"] = query
        captured["params"] = params
        return pd.DataFrame(
            {
                "game_date": ["2024-06-30", "2024-06-25"],
                "swstr_pct": [0.10, 0.20],
                "csw_pct": [0.30, 0.30],
                "whiff_pct": [0.25, 0.35],
                "chase_pct": [0.28, 0.32],
                "putaway_pct": [0.20, 0.20],
                "avg_fastball_velo": [95.0, 97.0],
            }
        )

    with patch.object(extractor, "_safe_query", side_effect=fake_query):
        feats = extractor.extract("Ace", "2024-07-01", stat_type="STRIKEOUTS")

    assert "statcast_pitcher_games" in captured["query"]
    assert "sg.game_date < %s" in captured["query"]
    assert captured["params"] == ("Ace", "2024-07-01")
    # L10 mean of swstr = (0.10+0.20)/2 = 0.15
    assert feats["statcast_pitcher_swstr_pct_L10"] == pytest.approx(0.15)
    assert feats["statcast_pitcher_avg_fastball_velo_L10"] == pytest.approx(96.0)
    assert feats["statcast_pitcher_games_sample"] == 2.0


def test_batter_routing_uses_batter_table(extractor):
    captured = {}

    def fake_query(query, params, conn=None):
        captured["query"] = query
        return pd.DataFrame(
            {
                "game_date": ["2024-06-30", "2024-06-25"],
                "avg_exit_velo": [90.0, 94.0],
                "hard_hit_pct": [0.40, 0.60],
                "barrel_pct": [0.05, 0.15],
                "sweet_spot_pct": [0.30, 0.40],
                "xba": [0.25, 0.35],
                "xwoba": [0.30, 0.40],
                "xslg": [0.40, 0.60],
                "whiff_pct": [0.20, 0.30],
                "chase_pct": [0.25, 0.35],
            }
        )

    with patch.object(extractor, "_safe_query", side_effect=fake_query):
        feats = extractor.extract("Slugger", "2024-07-01", stat_type="HITS")

    assert "statcast_batter_games" in captured["query"]
    assert feats["statcast_batter_avg_exit_velo_L10"] == pytest.approx(92.0)
    assert feats["statcast_batter_xwoba_L10"] == pytest.approx(0.35)
    assert feats["statcast_batter_games_sample"] == 2.0


def test_total_bases_routes_to_batter(extractor):
    captured = {}
    with patch.object(
        extractor,
        "_safe_query",
        side_effect=lambda q, p, conn=None: captured.update(q=q) or None,
    ):
        extractor.extract("Slugger", "2024-07-01", stat_type="TOTAL_BASES")
    assert "statcast_batter_games" in captured["q"]


def test_player_id_skips_name_join(extractor):
    captured = {}

    def fake_query(query, params, conn=None):
        captured["query"] = query
        captured["params"] = params
        return pd.DataFrame(
            {
                "game_date": ["2024-06-30"],
                "swstr_pct": [0.12],
                "csw_pct": [0.30],
                "whiff_pct": [0.25],
                "chase_pct": [0.28],
                "putaway_pct": [0.20],
                "avg_fastball_velo": [95.0],
            }
        )

    with patch.object(extractor, "_safe_query", side_effect=fake_query):
        extractor.extract("Ace", "2024-07-01", stat_type="STRIKEOUTS", player_id=12345)

    assert "JOIN" not in captured["query"]
    assert "sg.player_id = %s" in captured["query"]
    assert captured["params"] == (12345, "2024-07-01")


def test_season_window_uses_all_rows(extractor):
    # 12 games: L10 mean differs from season (all 12) mean.
    swstr = [float(i) / 100 for i in range(1, 13)]  # 0.01 .. 0.12 (DESC order as-is)
    df = pd.DataFrame(
        {
            "game_date": [f"2024-06-{30 - i:02d}" for i in range(12)],
            "swstr_pct": swstr,
            "csw_pct": [0.30] * 12,
            "whiff_pct": [0.25] * 12,
            "chase_pct": [0.28] * 12,
            "putaway_pct": [0.20] * 12,
            "avg_fastball_velo": [95.0] * 12,
        }
    )
    with patch.object(extractor, "_safe_query", return_value=df):
        feats = extractor.extract("Ace", "2024-07-01", stat_type="STRIKEOUTS")

    l10 = sum(swstr[:10]) / 10
    season = sum(swstr) / 12
    assert feats["statcast_pitcher_swstr_pct_L10"] == pytest.approx(l10)
    assert feats["statcast_pitcher_swstr_pct_season"] == pytest.approx(season)
    assert l10 != pytest.approx(season)


def test_extract_from_games_batter(extractor):
    games = [
        {
            "game_date": "2024-06-30",
            "avg_exit_velo": 92.0,
            "hard_hit_pct": 0.5,
            "barrel_pct": 0.1,
            "sweet_spot_pct": 0.35,
            "xba": 0.3,
            "xwoba": 0.35,
            "xslg": 0.5,
            "whiff_pct": 0.2,
            "chase_pct": 0.3,
        },
    ]
    feats = extractor.extract_from_games(games, "2024-07-01", stat_type="HITS")
    assert feats["statcast_batter_avg_exit_velo_L10"] == pytest.approx(92.0)
    assert feats["statcast_batter_games_sample"] == 1.0


def test_extract_from_games_empty(extractor):
    assert extractor.extract_from_games([], "2024-07-01") == StatcastFeatureExtractor.get_defaults()


def test_missing_metric_column_skipped(extractor):
    # A games frame missing some metric columns -> those metrics keep defaults
    # (the ``m not in df.columns`` continue branch), present ones compute.
    games = [{"game_date": "2024-06-30", "swstr_pct": 0.18}]
    defaults = StatcastFeatureExtractor.get_defaults()
    feats = extractor.extract_from_games(games, "2024-07-01", stat_type="STRIKEOUTS")
    assert feats["statcast_pitcher_swstr_pct_L10"] == pytest.approx(0.18)
    # csw_pct column absent -> default retained
    assert feats["statcast_pitcher_csw_pct_L10"] == pytest.approx(
        defaults["statcast_pitcher_csw_pct_L10"]
    )


def test_nan_metric_falls_back_to_default(extractor):
    # avg_fastball_velo all NaN -> default kept, but sample still counts.
    df = pd.DataFrame(
        {
            "game_date": ["2024-06-30"],
            "swstr_pct": [0.12],
            "csw_pct": [0.30],
            "whiff_pct": [0.25],
            "chase_pct": [0.28],
            "putaway_pct": [0.20],
            "avg_fastball_velo": [float("nan")],
        }
    )
    defaults = StatcastFeatureExtractor.get_defaults()
    with patch.object(extractor, "_safe_query", return_value=df):
        feats = extractor.extract("Ace", "2024-07-01", stat_type="STRIKEOUTS")
    assert feats["statcast_pitcher_avg_fastball_velo_L10"] == pytest.approx(
        defaults["statcast_pitcher_avg_fastball_velo_L10"]
    )
    assert feats["statcast_pitcher_swstr_pct_L10"] == pytest.approx(0.12)
