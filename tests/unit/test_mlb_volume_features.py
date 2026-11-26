"""
Unit tests for mlb.features.extractors.volume_features.

DB mocked via ``_safe_query``. Tests cover: lineup-slot resolution (announced
current slot vs recent typical mode), computed expected-PA mapping,
slot_known flagging, opposing-bullpen FIP/K9 math (with a computed season FIP
constant), the live-refresh path, leakage wiring, and defaults.
"""

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from mlb.features.extractors.sabermetric_features import FALLBACK_FIP_CONSTANT
from mlb.features.extractors.volume_features import (
    DEFAULT_EXPECTED_PA,
    FIP_CONSTANT,
    SLOT_EXPECTED_PA,
    SLOT_EXPECTED_PA_SNAPSHOT,
    VolumeFeatureExtractor,
    load_computed_expected_pa,
)


@pytest.fixture
def extractor():
    return VolumeFeatureExtractor(conn=MagicMock())


def _slot_df(slots):
    return pd.DataFrame({"batting_order": slots})


def _bullpen_df(games=12, ip=40.0, k=44.0, bb=14.0, hr=4.0):
    return pd.DataFrame({"games": [games], "ip": [ip], "k": [k], "bb": [bb], "hr": [hr]})


def _league_row(er=2000.0, ip=4000.0, hr=500.0, bb=1400.0, k=4000.0, hr_rows=4000.0):
    return pd.DataFrame(
        {"er": [er], "ip": [ip], "hr": [hr], "bb": [bb], "k": [k], "hr_rows": [hr_rows]}
    )


def _computed_const():
    lg_era = 9.0 * 2000.0 / 4000.0
    return lg_era - (13 * 500 + 3 * 1400 - 2 * 4000) / 4000.0


def _route(slot_df, bullpen_df, league_df=None):
    """side_effect routing slot / bullpen / league-constant queries."""
    league_df = league_df if league_df is not None else _league_row()

    def fake_query(query, params, conn=None):
        if "home_runs_allowed IS NOT NULL" in query and "EXTRACT(YEAR" in query:
            return league_df
        if "pgl.team = %s" in query:
            return bullpen_df
        return slot_df

    return fake_query


# --------------------------------------------------------------------------- #
# Plumbing
# --------------------------------------------------------------------------- #
def test_feature_names_in_defaults(extractor):
    defaults = VolumeFeatureExtractor.get_defaults()
    for name in VolumeFeatureExtractor.FEATURE_NAMES:
        assert name in defaults


def test_fip_constant_reexport_is_fallback():
    assert FIP_CONSTANT == FALLBACK_FIP_CONSTANT


def test_default_expected_pa_is_mean_of_curve():
    expected = round(sum(SLOT_EXPECTED_PA.values()) / len(SLOT_EXPECTED_PA), 4)
    assert DEFAULT_EXPECTED_PA == expected


def test_slot_curve_monotonic_nonincreasing():
    vals = [SLOT_EXPECTED_PA[s] for s in range(1, 10)]
    assert all(vals[i] >= vals[i + 1] for i in range(len(vals) - 1))


def test_no_inputs_returns_defaults(extractor):
    with patch.object(extractor, "_safe_query", return_value=None):
        feats = extractor.extract("Slugger", "2024-07-01", stat_type="HITS")
    assert feats == VolumeFeatureExtractor.get_defaults()


# --------------------------------------------------------------------------- #
# Lineup slot / expected PA
# --------------------------------------------------------------------------- #
def test_recent_typical_slot_is_mode(extractor):
    with patch.object(extractor, "_safe_query", return_value=_slot_df([2, 2, 2, 5, 3])):
        feats = extractor.extract("Slugger", "2024-07-01", stat_type="HITS")
    assert feats["volume_recent_typical_slot"] == 2.0
    assert feats["volume_lineup_slot"] == 2.0
    assert feats["volume_slot_known"] == 1.0
    assert feats["volume_slot_games_sample"] == 5.0
    assert feats["volume_expected_pa"] == SLOT_EXPECTED_PA[2]


def test_announced_slot_overrides_typical(extractor):
    with patch.object(extractor, "_safe_query", return_value=_slot_df([7, 7, 7])):
        feats = extractor.extract("Slugger", "2024-07-01", stat_type="HITS", lineup_slot=1)
    assert feats["volume_recent_typical_slot"] == 7.0
    assert feats["volume_lineup_slot"] == 1.0
    assert feats["volume_expected_pa"] == SLOT_EXPECTED_PA[1]


def test_announced_slot_no_history(extractor):
    with patch.object(extractor, "_safe_query", return_value=None):
        feats = extractor.extract("Rookie", "2024-07-01", stat_type="HITS", lineup_slot=4)
    assert feats["volume_lineup_slot"] == 4.0
    assert feats["volume_slot_known"] == 1.0
    assert feats["volume_recent_typical_slot"] == 0.0
    assert feats["volume_expected_pa"] == SLOT_EXPECTED_PA[4]


def test_invalid_announced_slot_falls_back_to_typical(extractor):
    with patch.object(extractor, "_safe_query", return_value=_slot_df([3, 3])):
        feats = extractor.extract("Slugger", "2024-07-01", stat_type="HITS", lineup_slot=99)
    assert feats["volume_lineup_slot"] == 3.0


def test_non_numeric_announced_slot_ignored(extractor):
    with patch.object(extractor, "_safe_query", return_value=_slot_df([6])):
        feats = extractor.extract(
            "Slugger", "2024-07-01", stat_type="HITS", lineup_slot="not-a-number"
        )
    assert feats["volume_lineup_slot"] == 6.0


def test_slot_query_is_pre_date(extractor):
    captured = {}

    def fake_query(query, params, conn=None):
        captured["query"] = query
        captured["params"] = params
        return _slot_df([1])

    with patch.object(extractor, "_safe_query", side_effect=fake_query):
        extractor.extract("Slugger", "2024-07-01", stat_type="HITS")

    assert "bls.game_date < %s" in captured["query"]
    assert captured["params"] == ("Slugger", "2024-07-01")


def test_slot_rows_all_null_no_slot(extractor):
    with patch.object(extractor, "_safe_query", return_value=_slot_df([None, None])):
        feats = extractor.extract("Ghost", "2024-07-01", stat_type="HITS")
    assert feats["volume_slot_known"] == 0.0
    assert feats["volume_expected_pa"] == DEFAULT_EXPECTED_PA


def test_pitcher_stat_skips_batter_slot(extractor):
    calls = {"n": 0}

    def fake_query(query, params, conn=None):
        calls["n"] += 1
        assert "batter_lineup_slots" not in query
        if "home_runs_allowed IS NOT NULL" in query and "EXTRACT(YEAR" in query:
            return _league_row()
        return _bullpen_df()

    with patch.object(extractor, "_safe_query", side_effect=fake_query):
        extractor.extract("Ace", "2024-07-01", stat_type="STRIKEOUTS", opponent="NYY")
    # bullpen query + league-constant query (no slot query)
    assert calls["n"] == 2


# --------------------------------------------------------------------------- #
# Opposing bullpen FIP
# --------------------------------------------------------------------------- #
def test_opp_bullpen_fip_math_uses_computed_constant(extractor):
    with patch.object(extractor, "_safe_query", side_effect=_route(_slot_df([2]), _bullpen_df())):
        feats = extractor.extract("Slugger", "2024-07-01", stat_type="HITS", opponent="NYY")
    const = _computed_const()
    expected_fip = (13 * 4 + 3 * 14 - 2 * 44) / 40.0 + const
    assert feats["volume_opp_bullpen_fip"] == pytest.approx(expected_fip)
    assert feats["volume_opp_bullpen_k_per_9"] == pytest.approx(44 / 40.0 * 9.0)
    assert feats["volume_opp_bullpen_games_sample"] == 12.0


def test_opp_bullpen_excludes_starter_in_query(extractor):
    captured = {}

    def fake_query(query, params, conn=None):
        if "home_runs_allowed IS NOT NULL" in query and "EXTRACT(YEAR" in query:
            return _league_row()
        if "pgl.team = %s" in query:
            captured["query"] = query
            captured["params"] = params
        return _bullpen_df()

    with patch.object(extractor, "_safe_query", side_effect=fake_query):
        extractor.extract("Ace", "2024-07-01", stat_type="STRIKEOUTS", opponent="BOS")

    assert "ORDER BY sp.batters_faced DESC" in captured["query"]
    assert "pgl.game_date < %s" in captured["query"]
    assert captured["params"][0] == "BOS"


def test_opp_bullpen_zero_games_keeps_defaults(extractor):
    with patch.object(
        extractor, "_safe_query", side_effect=_route(_slot_df([2]), _bullpen_df(games=0))
    ):
        feats = extractor.extract("Slugger", "2024-07-01", stat_type="HITS", opponent="NYY")
    assert feats["volume_opp_bullpen_fip"] == 4.20


def test_opp_bullpen_none_df_keeps_defaults(extractor):
    def fake_query(query, params, conn=None):
        if "home_runs_allowed IS NOT NULL" in query and "EXTRACT(YEAR" in query:
            return _league_row()
        if "pgl.team = %s" in query:
            return None
        return _slot_df([2])

    with patch.object(extractor, "_safe_query", side_effect=fake_query):
        feats = extractor.extract("Slugger", "2024-07-01", stat_type="HITS", opponent="NYY")
    assert feats["volume_opp_bullpen_fip"] == 4.20


def test_opp_bullpen_zero_ip_keeps_default_fip(extractor):
    with patch.object(
        extractor, "_safe_query", side_effect=_route(_slot_df([2]), _bullpen_df(ip=0.0))
    ):
        feats = extractor.extract("Slugger", "2024-07-01", stat_type="HITS", opponent="NYY")
    assert feats["volume_opp_bullpen_fip"] == 4.20
    assert feats["volume_opp_bullpen_games_sample"] == 12.0


def test_season_fip_constant_cached(extractor):
    calls = {"n": 0}

    def fake_query(query, params, conn=None):
        calls["n"] += 1
        return _league_row()

    with patch.object(extractor, "_safe_query", side_effect=fake_query):
        c1 = extractor._season_fip_constant(2024)
        c2 = extractor._season_fip_constant(2024)
    assert c1 == c2
    assert calls["n"] == 1


# --------------------------------------------------------------------------- #
# Live refresh of the expected-PA curve
# --------------------------------------------------------------------------- #
def test_load_computed_expected_pa_replaces_curve():
    saved_snap = dict(SLOT_EXPECTED_PA_SNAPSHOT)
    saved_flat = dict(SLOT_EXPECTED_PA)
    try:
        conn = MagicMock()
        cur = conn.cursor.return_value
        cur.fetchall.return_value = [
            (1, 4.50, 100),
            (2, 4.40, 100),
            (3, None, 100),  # NULL mean -> skipped
        ]
        n = load_computed_expected_pa(conn, window_label="rolling_2024_2026")
        assert n == 2
        assert SLOT_EXPECTED_PA[1] == 4.50
        assert SLOT_EXPECTED_PA[2] == 4.40
        assert SLOT_EXPECTED_PA_SNAPSHOT[1] == (4.50, 100)
        assert cur.execute.call_args[0][1] == ("rolling_2024_2026",)
    finally:
        SLOT_EXPECTED_PA_SNAPSHOT.clear()
        SLOT_EXPECTED_PA_SNAPSHOT.update(saved_snap)
        SLOT_EXPECTED_PA.clear()
        SLOT_EXPECTED_PA.update(saved_flat)


def test_load_computed_expected_pa_empty_keeps_curve():
    saved_flat = dict(SLOT_EXPECTED_PA)
    conn = MagicMock()
    conn.cursor.return_value.fetchall.return_value = []
    n = load_computed_expected_pa(conn)
    assert n == 0
    assert SLOT_EXPECTED_PA == saved_flat
