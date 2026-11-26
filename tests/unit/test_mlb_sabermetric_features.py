"""
Unit tests for mlb.features.extractors.sabermetric_features.

The DB is mocked: ``_safe_query`` returns hand-built single-row aggregate
DataFrames (matching the SUM/COUNT shape the extractor queries). Tests assert the
FIP math (with a computed season constant), wOBA math on its components,
the woba_complete flag, stat type routing, leakage wiring, and defaults.
"""

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from mlb.features.extractors.sabermetric_features import (
    FALLBACK_FIP_CONSTANT,
    WOBA_WEIGHTS,
    SabermetricFeatureExtractor,
    season_fip_constant,
)


@pytest.fixture
def extractor():
    return SabermetricFeatureExtractor(conn=MagicMock())


def _pitcher_row(games=10, ip=60.0, k=70.0, bb=18.0, bf=240.0, hr=8.0):
    return pd.DataFrame(
        {"games": [games], "ip": [ip], "k": [k], "bb": [bb], "bf": [bf], "hr": [hr]}
    )


def _league_row(er=2000.0, ip=4000.0, hr=500.0, bb=1400.0, k=4000.0, hr_rows=4000.0):
    return pd.DataFrame(
        {"er": [er], "ip": [ip], "hr": [hr], "bb": [bb], "k": [k], "hr_rows": [hr_rows]}
    )


def _batter_row(
    games=15,
    ab=55.0,
    h=18.0,
    tb=30.0,
    bb=8.0,
    k=12.0,
    pa=64.0,
    hr=3.0,
    doubles=4.0,
    triples=1.0,
    hbp=1.0,
    ibb=1.0,
    sf=1.0,
    xbh_rows=15.0,
):
    return pd.DataFrame(
        {
            "games": [games],
            "ab": [ab],
            "h": [h],
            "tb": [tb],
            "bb": [bb],
            "k": [k],
            "pa": [pa],
            "hr": [hr],
            "doubles": [doubles],
            "triples": [triples],
            "hbp": [hbp],
            "ibb": [ibb],
            "sf": [sf],
            "xbh_rows": [xbh_rows],
        }
    )


def _route_pitcher(league_df, pitcher_df):
    """Return league_df for the season-constant query, pitcher_df otherwise."""

    def fake_query(query, params, conn=None):
        if "EXTRACT(YEAR FROM game_date)" in query and "home_runs_allowed IS NOT NULL" in query:
            return league_df
        return pitcher_df

    return fake_query


# --------------------------------------------------------------------------- #
# Plumbing
# --------------------------------------------------------------------------- #
def test_feature_names_in_defaults(extractor):
    defaults = SabermetricFeatureExtractor.get_defaults()
    for name in SabermetricFeatureExtractor.FEATURE_NAMES:
        assert name in defaults


def test_dropped_proxies_not_in_feature_names():
    names = SabermetricFeatureExtractor.FEATURE_NAMES
    assert "saber_pitcher_xfip" not in names
    assert "saber_batter_wrcplus_proxy" not in names
    assert "saber_batter_woba_proxy" not in names


def test_no_history_returns_defaults_with_fip_const(extractor):
    # League query returns None too -> fallback constant surfaced.
    with patch.object(extractor, "_safe_query", return_value=None):
        feats = extractor.extract("Nobody", "2024-07-01", stat_type="STRIKEOUTS")
    assert feats["saber_pitcher_fip"] == 4.00
    assert feats["saber_pitcher_fip_constant"] == FALLBACK_FIP_CONSTANT


def test_no_history_batter_returns_defaults(extractor):
    with patch.object(extractor, "_safe_query", return_value=None):
        feats = extractor.extract("Nobody", "2024-07-01", stat_type="HITS")
    assert feats == SabermetricFeatureExtractor.get_defaults()


def test_zero_games_returns_defaults_with_fip_const(extractor):
    fake = _route_pitcher(_league_row(), _pitcher_row(games=0))
    with patch.object(extractor, "_safe_query", side_effect=fake):
        feats = extractor.extract("Nobody", "2024-07-01", stat_type="STRIKEOUTS")
    # Computed constant from the league row is surfaced even with no pitcher games.
    lg_era = 9.0 * 2000.0 / 4000.0
    expected_const = lg_era - (13 * 500 + 3 * 1400 - 2 * 4000) / 4000.0
    assert feats["saber_pitcher_fip_constant"] == pytest.approx(expected_const)
    assert feats["saber_pitcher_fip"] == 4.00


def test_zero_games_batter_returns_defaults(extractor):
    with patch.object(extractor, "_safe_query", return_value=_batter_row(games=0)):
        feats = extractor.extract("Nobody", "2024-07-01", stat_type="HITS")
    assert feats == SabermetricFeatureExtractor.get_defaults()


# --------------------------------------------------------------------------- #
# Season FIP constant
# --------------------------------------------------------------------------- #
def test_season_fip_constant_computed(extractor):
    with patch.object(extractor, "_safe_query", return_value=_league_row()):
        const = season_fip_constant(extractor, 2024)
    lg_era = 9.0 * 2000.0 / 4000.0
    expected = lg_era - (13 * 500 + 3 * 1400 - 2 * 4000) / 4000.0
    assert const == pytest.approx(expected)


def test_season_fip_constant_no_rows_fallback(extractor):
    with patch.object(extractor, "_safe_query", return_value=None):
        assert season_fip_constant(extractor, 2024) == FALLBACK_FIP_CONSTANT


def test_season_fip_constant_zero_ip_fallback(extractor):
    with patch.object(extractor, "_safe_query", return_value=_league_row(ip=0.0)):
        assert season_fip_constant(extractor, 2024) == FALLBACK_FIP_CONSTANT


def test_season_fip_constant_zero_hr_rows_fallback(extractor):
    with patch.object(extractor, "_safe_query", return_value=_league_row(hr_rows=0.0)):
        assert season_fip_constant(extractor, 2024) == FALLBACK_FIP_CONSTANT


def test_season_fip_constant_cached(extractor):
    calls = {"n": 0}

    def fake_query(query, params, conn=None):
        calls["n"] += 1
        return _league_row()

    with patch.object(extractor, "_safe_query", side_effect=fake_query):
        c1 = extractor._season_fip_constant(2024)
        c2 = extractor._season_fip_constant(2024)
    assert c1 == c2
    assert calls["n"] == 1  # second call served from cache


# --------------------------------------------------------------------------- #
# Pitcher math
# --------------------------------------------------------------------------- #
def test_pitcher_routing_and_leakage(extractor):
    captured = {}

    def fake_query(query, params, conn=None):
        if "home_runs_allowed IS NOT NULL" in query and "EXTRACT(YEAR" in query:
            return _league_row()
        captured["query"] = query
        captured["params"] = params
        return _pitcher_row()

    with patch.object(extractor, "_safe_query", side_effect=fake_query):
        feats = extractor.extract("Ace", "2024-07-01", stat_type="STRIKEOUTS")

    assert "pitcher_game_logs" in captured["query"]
    assert "pgl.game_date < %s" in captured["query"]
    assert captured["params"][0] == "Ace"
    assert captured["params"][1] == "2024-07-01"
    assert feats["saber_pitcher_games_sample"] == 10.0


def test_pitcher_fip_math_uses_computed_constant(extractor):
    with patch.object(
        extractor, "_safe_query", side_effect=_route_pitcher(_league_row(), _pitcher_row())
    ):
        feats = extractor.extract("Ace", "2024-07-01", stat_type="STRIKEOUTS")
    lg_era = 9.0 * 2000.0 / 4000.0
    const = lg_era - (13 * 500 + 3 * 1400 - 2 * 4000) / 4000.0
    expected_fip = (13 * 8 + 3 * 18 - 2 * 70) / 60.0 + const
    assert feats["saber_pitcher_fip"] == pytest.approx(expected_fip)
    assert feats["saber_pitcher_fip_constant"] == pytest.approx(const)
    assert feats["saber_pitcher_hr_per_9"] == pytest.approx(8 / 60.0 * 9.0)


def test_pitcher_k_minus_bb_pct(extractor):
    with patch.object(
        extractor, "_safe_query", side_effect=_route_pitcher(_league_row(), _pitcher_row())
    ):
        feats = extractor.extract("Ace", "2024-07-01", stat_type="STRIKEOUTS")
    assert feats["saber_pitcher_k_pct"] == pytest.approx(70 / 240.0)
    assert feats["saber_pitcher_walk_rate"] == pytest.approx(18 / 240.0)
    assert feats["saber_pitcher_k_minus_bb_pct"] == pytest.approx((70 - 18) / 240.0)


def test_pitcher_zero_ip_keeps_default_fip(extractor):
    with patch.object(
        extractor, "_safe_query", side_effect=_route_pitcher(_league_row(), _pitcher_row(ip=0.0))
    ):
        feats = extractor.extract("Ace", "2024-07-01", stat_type="STRIKEOUTS")
    assert feats["saber_pitcher_fip"] == 4.00  # default retained


def test_pitcher_zero_bf_keeps_default_rates(extractor):
    with patch.object(
        extractor, "_safe_query", side_effect=_route_pitcher(_league_row(), _pitcher_row(bf=0.0))
    ):
        feats = extractor.extract("Ace", "2024-07-01", stat_type="STRIKEOUTS")
    assert feats["saber_pitcher_k_pct"] == 0.22


# --------------------------------------------------------------------------- #
# Batter routing + wOBA math
# --------------------------------------------------------------------------- #
def test_batter_routing_uses_batter_table(extractor):
    captured = {}

    def fake_query(query, params, conn=None):
        captured["query"] = query
        return _batter_row()

    with patch.object(extractor, "_safe_query", side_effect=fake_query):
        feats = extractor.extract("Slugger", "2024-07-01", stat_type="TOTAL_BASES")

    assert "batter_game_logs" in captured["query"]
    assert feats["saber_batter_games_sample"] == 15.0


def test_batter_slash_and_iso(extractor):
    with patch.object(extractor, "_safe_query", return_value=_batter_row()):
        feats = extractor.extract("Slugger", "2024-07-01", stat_type="HITS")
    avg = 18 / 55.0
    slg = 30 / 55.0
    # Complete data -> OBP uses (H+BB+HBP)/(AB+BB+HBP+SF).
    obp = (18 + 8 + 1) / (55 + 8 + 1 + 1)
    assert feats["saber_batter_slg"] == pytest.approx(slg)
    assert feats["saber_batter_iso"] == pytest.approx(slg - avg)
    assert feats["saber_batter_obp"] == pytest.approx(obp)


def test_batter_real_woba_complete(extractor):
    with patch.object(extractor, "_safe_query", return_value=_batter_row()):
        feats = extractor.extract("Slugger", "2024-07-01", stat_type="TOTAL_BASES")
    singles = 18 - 4 - 1 - 3
    u_bb = 8 - 1
    num = (
        WOBA_WEIGHTS["wBB"] * u_bb
        + WOBA_WEIGHTS["wHBP"] * 1
        + WOBA_WEIGHTS["w1B"] * singles
        + WOBA_WEIGHTS["w2B"] * 4
        + WOBA_WEIGHTS["w3B"] * 1
        + WOBA_WEIGHTS["wHR"] * 3
    )
    denom = 55 + 8 - 1 + 1 + 1
    assert feats["saber_batter_woba"] == pytest.approx(round(num / denom, 4))
    assert feats["saber_batter_woba_complete"] == 1.0


def test_batter_woba_incomplete_when_xbh_missing(extractor):
    # xbh_rows < games -> components not backfilled -> fallback + flag 0.
    row = _batter_row(xbh_rows=0.0)
    with patch.object(extractor, "_safe_query", return_value=row):
        feats = extractor.extract("Slugger", "2024-07-01", stat_type="HITS")
    obp = (18 + 8) / 64.0  # PA-denominator OBP path
    slg = 30 / 55.0
    assert feats["saber_batter_obp"] == pytest.approx(obp)
    assert feats["saber_batter_woba"] == pytest.approx(round(0.7 * obp + 0.3 * slg, 4))
    assert feats["saber_batter_woba_complete"] == 0.0


def test_batter_walk_strikeout_rates(extractor):
    with patch.object(extractor, "_safe_query", return_value=_batter_row()):
        feats = extractor.extract("Slugger", "2024-07-01", stat_type="HITS")
    assert feats["saber_batter_walk_rate"] == pytest.approx(8 / 64.0)
    assert feats["saber_batter_strikeout_rate"] == pytest.approx(12 / 64.0)


def test_batter_zero_ab_and_pa_keeps_defaults(extractor):
    row = _batter_row(
        ab=0.0,
        h=0.0,
        tb=0.0,
        bb=0.0,
        k=0.0,
        pa=0.0,
        hr=0.0,
        doubles=0.0,
        triples=0.0,
        hbp=0.0,
        ibb=0.0,
        sf=0.0,
        xbh_rows=0.0,
    )
    with patch.object(extractor, "_safe_query", return_value=row):
        feats = extractor.extract("Slugger", "2024-07-01", stat_type="HITS")
    assert feats["saber_batter_woba"] == 0.320  # default retained
    assert feats["saber_batter_woba_complete"] == 0.0
    assert feats["saber_batter_iso"] == 0.0


def test_batter_complete_zero_denominator_falls_back(extractor):
    # Complete components present but AB+BB-IBB+SF+HBP == 0 -> use fallback path.
    row = _batter_row(
        ab=0.0,
        h=0.0,
        tb=0.0,
        bb=0.0,
        k=0.0,
        pa=5.0,
        hr=0.0,
        doubles=0.0,
        triples=0.0,
        hbp=0.0,
        ibb=0.0,
        sf=0.0,
        xbh_rows=15.0,
    )
    with patch.object(extractor, "_safe_query", return_value=row):
        feats = extractor.extract("Walker", "2024-07-01", stat_type="HITS")
    # obp (complete path, denom 0) -> 0 ; slg 0 -> fallback 0.7*0+0.3*0 = 0, flag 0
    assert feats["saber_batter_woba_complete"] == 0.0


def test_batter_complete_obp_zero_denom(extractor):
    # complete=True but AB+BB+HBP+SF == 0 -> obp denominator zero branch.
    row = _batter_row(
        ab=0.0,
        h=0.0,
        tb=0.0,
        bb=0.0,
        k=0.0,
        pa=3.0,
        hr=0.0,
        doubles=0.0,
        triples=0.0,
        hbp=0.0,
        ibb=0.0,
        sf=0.0,
        xbh_rows=15.0,
    )
    with patch.object(extractor, "_safe_query", return_value=row):
        feats = extractor.extract("Ghost", "2024-07-01", stat_type="HITS")
    assert feats["saber_batter_obp"] == 0.0
