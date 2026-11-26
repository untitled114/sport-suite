"""
Unit tests for mlb.features.extractors.h2h_features.H2HFeatureExtractor.

The DB is mocked: ``_safe_query`` returns hand-built encounter DataFrames.
Tests assert the real-BvP summing math, the leakage-safe as-of cutoff
(game_date < %s), id-vs-name resolution paths, explicit missingness
(bvp_known / bvp_real_pa), and defaults.
"""

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from mlb.features.extractors.h2h_features import H2HFeatureExtractor


@pytest.fixture
def extractor():
    return H2HFeatureExtractor(conn=MagicMock())


def _enc(pa, hits, k, bb, tb):
    return pd.DataFrame([{"pa": pa, "hits": hits, "k": k, "bb": bb, "tb": tb}])


def test_feature_names_in_defaults():
    defaults = H2HFeatureExtractor.get_defaults()
    for name in H2HFeatureExtractor.FEATURE_NAMES:
        assert name in defaults


def test_no_history_returns_defaults_with_known_zero(extractor):
    with patch.object(extractor, "_safe_query", return_value=None):
        feats = extractor.extract("Batter", "2024-07-01", batter_id=1, pitcher_id=2)
    assert feats == H2HFeatureExtractor.get_defaults()
    assert feats["bvp_known"] == 0.0
    assert feats["bvp_real_pa"] == 0.0


def test_id_join_leakage_cutoff_and_math(extractor):
    captured = {}

    def fake_query(query, params, conn=None):
        captured["query"] = query
        captured["params"] = params
        # 2 encounters: total pa=10, hits=4, k=3, bb=2, tb=7
        return pd.DataFrame(
            [
                {"pa": 6, "hits": 2, "k": 2, "bb": 1, "tb": 4},
                {"pa": 4, "hits": 2, "k": 1, "bb": 1, "tb": 3},
            ]
        )

    with patch.object(extractor, "_safe_query", side_effect=fake_query):
        feats = extractor.extract("Batter", "2024-07-01", batter_id=111, pitcher_id=222)

    assert "be.game_date < %s" in captured["query"]
    assert "be.batter_id = %s" in captured["query"]
    assert "be.pitcher_id = %s" in captured["query"]
    assert "JOIN" not in captured["query"]
    # params order: game_date first, then batter_id, then pitcher_id
    assert captured["params"] == ("2024-07-01", 111, 222)

    pa, hits, k, bb, tb = 10, 4, 3, 2, 7
    ab = pa - bb  # 8
    assert feats["bvp_real_pa"] == pytest.approx(pa)
    assert feats["bvp_known"] == 1.0
    assert feats["bvp_k_rate"] == pytest.approx(k / pa)
    assert feats["bvp_avg"] == pytest.approx(hits / ab)
    assert feats["bvp_ops_proxy"] == pytest.approx((hits + bb) / pa + tb / pa)


def test_player_id_alias_resolves_batter(extractor):
    captured = {}
    with patch.object(
        extractor,
        "_safe_query",
        side_effect=lambda q, p, conn=None: captured.update(q=q, p=p) or _enc(3, 1, 1, 0, 2),
    ):
        feats = extractor.extract("Batter", "2024-07-01", player_id=5, opp_pitcher_id=9)
    assert captured["p"] == ("2024-07-01", 5, 9)
    assert feats["bvp_known"] == 1.0


def test_name_join_for_both_when_no_ids(extractor):
    captured = {}

    def fake_query(query, params, conn=None):
        captured["query"] = query
        captured["params"] = params
        return _enc(4, 2, 1, 0, 3)

    with patch.object(extractor, "_safe_query", side_effect=fake_query):
        extractor.extract("Carlos Peña", "2024-07-01", opp_pitcher_name="José Ramírez")

    assert "JOIN batter_profile" in captured["query"]
    assert "JOIN pitcher_profile" in captured["query"]
    assert "unaccent(bp.full_name)" in captured["query"]
    assert "unaccent(pp.full_name)" in captured["query"]
    assert captured["params"] == ("2024-07-01", "Carlos Peña", "José Ramírez")


def test_batter_id_with_pitcher_name(extractor):
    captured = {}
    with patch.object(
        extractor,
        "_safe_query",
        side_effect=lambda q, p, conn=None: captured.update(q=q, p=p) or _enc(2, 1, 0, 0, 1),
    ):
        extractor.extract("Batter", "2024-07-01", batter_id=7, opp_pitcher_name="Ace")
    assert "be.batter_id = %s" in captured["q"]
    assert "JOIN pitcher_profile" in captured["q"]
    assert captured["p"] == ("2024-07-01", 7, "Ace")


def test_no_pitcher_identifier_returns_defaults(extractor):
    # Batter id present but no pitcher id or name -> cannot identify -> defaults.
    called = {"q": False}

    def fake_query(*a, **k):
        called["q"] = True
        return _enc(5, 2, 1, 0, 3)

    with patch.object(extractor, "_safe_query", side_effect=fake_query):
        feats = extractor.extract("Batter", "2024-07-01", batter_id=7)
    assert called["q"] is False  # never queried
    assert feats == H2HFeatureExtractor.get_defaults()


def test_zero_pa_returns_defaults(extractor):
    # Rows exist but sum to 0 PA -> treated as no real history.
    with patch.object(extractor, "_safe_query", return_value=_enc(0, 0, 0, 0, 0)):
        feats = extractor.extract("Batter", "2024-07-01", batter_id=1, pitcher_id=2)
    assert feats["bvp_known"] == 0.0
    assert feats["bvp_real_pa"] == 0.0


def test_all_walks_avg_zero(extractor):
    # pa == bb -> ab == 0 -> bvp_avg falls to 0.0 (not div-by-zero).
    with patch.object(extractor, "_safe_query", return_value=_enc(2, 0, 0, 2, 0)):
        feats = extractor.extract("Batter", "2024-07-01", batter_id=1, pitcher_id=2)
    assert feats["bvp_known"] == 1.0
    assert feats["bvp_avg"] == pytest.approx(0.0)
    assert feats["bvp_real_pa"] == pytest.approx(2.0)
