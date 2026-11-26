"""
Tests for ``mlb.features.extractors.platoon_features``.
======================================================
Full hand-split batter platoon splits + opponent-lineup-vs-hand tendencies,
plus the reusable opposing-starter hand resolver.

The DB is fully mocked: ``_safe_query`` (extractor) and ``pd.read_sql_query``
(resolver) are patched to return small hand-built frames that exercise the split math, same-hand
selection, the ``game_date < %s`` leakage cutoff wiring, and sample-size /
``*_known`` missing-data handling.
"""

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from mlb.features.extractors.platoon_features import (
    HAND_L,
    HAND_R,
    OpposingStarterHandResolver,
    PlatoonFeatureExtractor,
)


# =============================================================================
# Schema sanity - FEATURE_NAMES exactly matches get_defaults keys
# =============================================================================
class TestSchema:
    def test_feature_names_match_defaults(self):
        defaults = PlatoonFeatureExtractor.get_defaults()
        assert set(PlatoonFeatureExtractor.FEATURE_NAMES) == set(defaults.keys())

    def test_feature_names_unique(self):
        names = PlatoonFeatureExtractor.FEATURE_NAMES
        assert len(names) == len(set(names))

    def test_expected_split_columns_present(self):
        names = set(PlatoonFeatureExtractor.FEATURE_NAMES)
        # Full vs-RHP / vs-LHP rate surface for the batter.
        for hand in ("rhp", "lhp"):
            for stat in (
                "avg",
                "obp_proxy",
                "slg_proxy",
                "ops_proxy",
                "iso_proxy",
                "k_rate",
                "bb_rate",
            ):
                assert f"batter_vs_{hand}_{stat}" in names
        # Same-hand copies + sample sizes.
        assert "batter_same_hand_ops_proxy" in names
        assert "batter_vs_hand_pa" in names
        assert "batter_vs_rhp_pa" in names
        assert "batter_vs_lhp_pa" in names
        # Opponent lineup vs hand (strikeouts).
        for hand in ("rhp", "lhp"):
            for stat in ("k_rate", "ops_proxy", "ba"):
                assert f"opp_lineup_vs_{hand}_{stat}" in names
        assert "opp_lineup_same_hand_k_rate" in names
        assert "opp_lineup_vs_hand_pa" in names


# =============================================================================
# OpposingStarterHandResolver
# =============================================================================
class TestHandResolver:
    def test_encode_hand_codes(self):
        assert OpposingStarterHandResolver.encode_hand("R") == HAND_R
        assert OpposingStarterHandResolver.encode_hand("r") == HAND_R
        assert OpposingStarterHandResolver.encode_hand("Right") == HAND_R
        assert OpposingStarterHandResolver.encode_hand("L") == HAND_L
        assert OpposingStarterHandResolver.encode_hand("left") == HAND_L
        assert OpposingStarterHandResolver.encode_hand("S") == 0.0
        assert OpposingStarterHandResolver.encode_hand("") == 0.0
        assert OpposingStarterHandResolver.encode_hand(None) == 0.0

    def test_resolve_throws_by_id(self):
        r = OpposingStarterHandResolver(MagicMock())
        df = pd.DataFrame([{"throws": "L"}])
        with patch("pandas.read_sql_query", return_value=df) as m:
            assert r.resolve_throws(pitcher_id=42) == "L"
        # id path used (param tuple carries the id)
        assert m.call_args.kwargs["params"] == (42,)

    def test_resolve_throws_by_name(self):
        r = OpposingStarterHandResolver(MagicMock())
        df = pd.DataFrame([{"throws": "R"}])
        with patch("pandas.read_sql_query", return_value=df):
            assert r.resolve_throws(pitcher_name="Ace Jones") == "R"

    def test_resolve_throws_no_args_returns_none(self):
        r = OpposingStarterHandResolver(MagicMock())
        assert r.resolve_throws() is None

    def test_resolve_throws_no_rows(self):
        r = OpposingStarterHandResolver(MagicMock())
        with patch("pandas.read_sql_query", return_value=pd.DataFrame()):
            assert r.resolve_throws(pitcher_id=1) is None

    def test_resolve_throws_null_value(self):
        r = OpposingStarterHandResolver(MagicMock())
        df = pd.DataFrame([{"throws": None}])
        with patch("pandas.read_sql_query", return_value=df):
            assert r.resolve_throws(pitcher_id=1) is None

    def test_resolve_throws_nan_value(self):
        r = OpposingStarterHandResolver(MagicMock())
        df = pd.DataFrame([{"throws": float("nan")}])
        with patch("pandas.read_sql_query", return_value=df):
            assert r.resolve_throws(pitcher_id=1) is None

    def test_query_handles_db_error(self):
        r = OpposingStarterHandResolver(MagicMock())
        with patch("pandas.read_sql_query", side_effect=ValueError("boom")):
            assert r.resolve_throws(pitcher_id=1) is None

    def test_resolve_from_schedule_by_game_id(self):
        r = OpposingStarterHandResolver(MagicMock())
        df = pd.DataFrame([{"throws": "L"}])
        with patch("pandas.read_sql_query", return_value=df) as m:
            assert r.resolve_from_schedule(game_id=999, opponent_team="BOS") == "L"
        assert m.call_args.kwargs["params"] == (999, "BOS")

    def test_resolve_from_schedule_by_date(self):
        r = OpposingStarterHandResolver(MagicMock())
        df = pd.DataFrame([{"throws": "R"}])
        with patch("pandas.read_sql_query", return_value=df) as m:
            out = r.resolve_from_schedule(game_id=None, opponent_team="BOS", game_date="2026-04-10")
        assert out == "R"
        assert m.call_args.kwargs["params"] == ("2026-04-10", "BOS")

    def test_resolve_from_schedule_no_key(self):
        r = OpposingStarterHandResolver(MagicMock())
        assert r.resolve_from_schedule(game_id=None, opponent_team=None) is None

    def test_resolve_from_schedule_no_rows(self):
        r = OpposingStarterHandResolver(MagicMock())
        with patch("pandas.read_sql_query", return_value=pd.DataFrame()):
            assert r.resolve_from_schedule(game_id=1, opponent_team="BOS") is None

    def test_resolve_from_schedule_null_value(self):
        r = OpposingStarterHandResolver(MagicMock())
        df = pd.DataFrame([{"throws": None}])
        with patch("pandas.read_sql_query", return_value=df):
            assert r.resolve_from_schedule(game_id=1, opponent_team="BOS") is None

    def test_resolve_hand_prefers_explicit(self):
        r = OpposingStarterHandResolver(MagicMock())
        # Explicit hand short-circuits - no DB query at all.
        with patch("pandas.read_sql_query") as m:
            assert r.resolve_hand(pitcher_id=1, pitcher_hand="L") == HAND_L
        m.assert_not_called()

    def test_resolve_hand_falls_back_to_profile(self):
        r = OpposingStarterHandResolver(MagicMock())
        df = pd.DataFrame([{"throws": "R"}])
        with patch("pandas.read_sql_query", return_value=df):
            assert r.resolve_hand(pitcher_id=7) == HAND_R

    def test_resolve_hand_falls_back_to_schedule(self):
        r = OpposingStarterHandResolver(MagicMock())

        calls = {"n": 0}

        def fake(query, conn, params=None):
            calls["n"] += 1
            if "pitcher_profile WHERE" in query or "WHERE player_id" in query:
                return pd.DataFrame()  # profile miss
            return pd.DataFrame([{"throws": "L"}])  # schedule hit

        with patch("pandas.read_sql_query", side_effect=fake):
            out = r.resolve_hand(pitcher_id=7, opponent_team="BOS", game_id=5)
        assert out == HAND_L
        assert calls["n"] == 2  # tried profile, then schedule

    def test_resolve_hand_unknown_returns_zero(self):
        r = OpposingStarterHandResolver(MagicMock())
        with patch("pandas.read_sql_query", return_value=pd.DataFrame()):
            assert r.resolve_hand(pitcher_id=7, opponent_team="BOS", game_id=5) == 0.0


# =============================================================================
# Batter full platoon splits
# =============================================================================
def _batter_rows():
    """Two vs-LHP games and two vs-RHP games with distinct, checkable totals."""
    return pd.DataFrame(
        [
            # vs LHP: AB 4 H 2 TB 4 BB 1 K 1 PA 5  +  AB 4 H 2 TB 6 BB 0 K 1 PA 4
            {
                "at_bats": 4,
                "hits": 2,
                "total_bases": 4,
                "walks": 1,
                "strikeouts": 1,
                "plate_appearances": 5,
                "opp_hand": "L",
            },
            {
                "at_bats": 4,
                "hits": 2,
                "total_bases": 6,
                "walks": 0,
                "strikeouts": 1,
                "plate_appearances": 4,
                "opp_hand": "L",
            },
            # vs RHP: AB 5 H 1 TB 1 BB 0 K 3 PA 5  +  AB 5 H 1 TB 1 BB 1 K 2 PA 6
            {
                "at_bats": 5,
                "hits": 1,
                "total_bases": 1,
                "walks": 0,
                "strikeouts": 3,
                "plate_appearances": 5,
                "opp_hand": "R",
            },
            {
                "at_bats": 5,
                "hits": 1,
                "total_bases": 1,
                "walks": 1,
                "strikeouts": 2,
                "plate_appearances": 6,
                "opp_hand": "R",
            },
        ]
    )


class TestBatterSplits:
    def test_vs_rhp_and_vs_lhp_math(self):
        ext = PlatoonFeatureExtractor(MagicMock())
        with patch.object(ext, "_safe_query", return_value=_batter_rows()):
            out = ext.extract("Slugger", "2026-04-10", "HITS", pitcher_hand="L")

        # vs LHP totals: AB 8, H 4, TB 10, BB 1, K 2, PA 9
        assert out["batter_vs_lhp_pa"] == pytest.approx(9.0)
        assert out["batter_vs_lhp_avg"] == pytest.approx(4 / 8)
        assert out["batter_vs_lhp_slg_proxy"] == pytest.approx(10 / 8)
        assert out["batter_vs_lhp_iso_proxy"] == pytest.approx((10 - 4) / 8)
        assert out["batter_vs_lhp_obp_proxy"] == pytest.approx((4 + 1) / (8 + 1))
        assert out["batter_vs_lhp_ops_proxy"] == pytest.approx((4 + 1) / 9 + 10 / 8)
        assert out["batter_vs_lhp_k_rate"] == pytest.approx(2 / 9)
        assert out["batter_vs_lhp_bb_rate"] == pytest.approx(1 / 9)
        assert out["batter_vs_lhp_known"] == 1.0

        # vs RHP totals: AB 10, H 2, TB 2, BB 1, K 5, PA 11
        assert out["batter_vs_rhp_pa"] == pytest.approx(11.0)
        assert out["batter_vs_rhp_avg"] == pytest.approx(2 / 10)
        assert out["batter_vs_rhp_slg_proxy"] == pytest.approx(2 / 10)
        assert out["batter_vs_rhp_iso_proxy"] == pytest.approx(0.0)
        assert out["batter_vs_rhp_k_rate"] == pytest.approx(5 / 11)
        assert out["batter_vs_rhp_known"] == 1.0
        assert out["batter_platoon_known"] == 1.0

    def test_same_hand_selects_lhp(self):
        ext = PlatoonFeatureExtractor(MagicMock())
        with patch.object(ext, "_safe_query", return_value=_batter_rows()):
            out = ext.extract("Slugger", "2026-04-10", "HITS", pitcher_hand="L")
        # same-hand == vs-LHP bucket
        assert out["batter_same_hand_avg"] == pytest.approx(out["batter_vs_lhp_avg"])
        assert out["batter_vs_hand_pa"] == pytest.approx(9.0)
        assert out["batter_same_hand_known"] == 1.0

    def test_same_hand_selects_rhp(self):
        ext = PlatoonFeatureExtractor(MagicMock())
        with patch.object(ext, "_safe_query", return_value=_batter_rows()):
            out = ext.extract("Slugger", "2026-04-10", "HITS", pitcher_hand="R")
        assert out["batter_same_hand_avg"] == pytest.approx(out["batter_vs_rhp_avg"])
        assert out["batter_vs_hand_pa"] == pytest.approx(11.0)
        assert out["batter_same_hand_known"] == 1.0

    def test_same_hand_unknown_no_selection(self):
        # opp hand can't be resolved -> same-hand bucket stays default, but full
        # vs-RHP / vs-LHP splits still computed.
        ext = PlatoonFeatureExtractor(MagicMock())
        with patch.object(ext.hand_resolver, "resolve_hand", return_value=0.0):
            with patch.object(ext, "_safe_query", return_value=_batter_rows()):
                out = ext.extract("Slugger", "2026-04-10", "HITS")
        assert out["batter_vs_hand_pa"] == 0.0
        assert out["batter_same_hand_known"] == 0.0
        # full splits still present
        assert out["batter_vs_lhp_known"] == 1.0
        assert out["batter_vs_rhp_known"] == 1.0

    def test_switch_hand_starter_rows_excluded(self):
        ext = PlatoonFeatureExtractor(MagicMock())
        df = pd.DataFrame(
            [
                {
                    "at_bats": 4,
                    "hits": 2,
                    "total_bases": 4,
                    "walks": 0,
                    "strikeouts": 0,
                    "plate_appearances": 4,
                    "opp_hand": "L",
                },
                # unknown/switch hand -> excluded from both buckets
                {
                    "at_bats": 4,
                    "hits": 4,
                    "total_bases": 8,
                    "walks": 0,
                    "strikeouts": 0,
                    "plate_appearances": 4,
                    "opp_hand": "S",
                },
            ]
        )
        with patch.object(ext, "_safe_query", return_value=df):
            out = ext.extract("Slugger", "2026-04-10", "HITS", pitcher_hand="L")
        # only the L row counts (PA 4), the S row dropped
        assert out["batter_vs_lhp_pa"] == pytest.approx(4.0)
        assert out["batter_vs_rhp_pa"] == 0.0

    def test_no_rows_keeps_defaults_and_flags_zero(self):
        ext = PlatoonFeatureExtractor(MagicMock())
        with patch.object(ext, "_safe_query", return_value=None):
            out = ext.extract("Slugger", "2026-04-10", "HITS", pitcher_hand="L")
        d = PlatoonFeatureExtractor.get_defaults()
        assert out["batter_vs_lhp_avg"] == d["batter_vs_lhp_avg"]
        assert out["batter_platoon_known"] == 0.0
        assert out["batter_vs_lhp_pa"] == 0.0
        assert out["batter_vs_hand_pa"] == 0.0

    def test_one_hand_only_other_stays_default(self):
        # Only vs-RHP history -> vs-LHP stays default + known 0, but platoon known.
        ext = PlatoonFeatureExtractor(MagicMock())
        df = pd.DataFrame(
            [
                {
                    "at_bats": 5,
                    "hits": 2,
                    "total_bases": 3,
                    "walks": 1,
                    "strikeouts": 1,
                    "plate_appearances": 6,
                    "opp_hand": "R",
                },
            ]
        )
        with patch.object(ext, "_safe_query", return_value=df):
            out = ext.extract("Slugger", "2026-04-10", "HITS", pitcher_hand="L")
        d = PlatoonFeatureExtractor.get_defaults()
        assert out["batter_vs_rhp_known"] == 1.0
        assert out["batter_vs_lhp_known"] == 0.0
        assert out["batter_vs_lhp_avg"] == d["batter_vs_lhp_avg"]
        # same-hand is L but no L data -> not known
        assert out["batter_same_hand_known"] == 0.0
        assert out["batter_platoon_known"] == 1.0

    def test_batter_bucket_keyed_on_pa(self):
        # Bucket is "known" only when it accumulates positive PA. A row with
        # plate_appearances 0 contributes nothing -> bucket stays empty/default.
        ext = PlatoonFeatureExtractor(MagicMock())
        df = pd.DataFrame(
            [
                {
                    "at_bats": 4,
                    "hits": 2,
                    "total_bases": 3,
                    "walks": 0,
                    "strikeouts": 1,
                    "plate_appearances": 0,
                    "opp_hand": "L",
                },
            ]
        )
        with patch.object(ext, "_safe_query", return_value=df):
            out = ext.extract("Slugger", "2026-04-10", "HITS", pitcher_hand="L")
        d = PlatoonFeatureExtractor.get_defaults()
        assert out["batter_vs_lhp_pa"] == 0.0
        assert out["batter_vs_lhp_known"] == 0.0
        assert out["batter_vs_lhp_avg"] == d["batter_vs_lhp_avg"]
        assert out["batter_platoon_known"] == 0.0

    def test_leakage_cutoff_in_query(self):
        ext = PlatoonFeatureExtractor(MagicMock())
        captured = {}

        def fake_query(query, params, conn=None):
            captured["query"] = query
            captured["params"] = params
            return None

        with patch.object(ext, "_safe_query", side_effect=fake_query):
            ext.extract("Slugger", "2026-04-10 19:05:00", "HITS", pitcher_hand="L")
        assert "bgl.game_date < %s" in captured["query"]
        # date normalized to YYYY-MM-DD and passed as the cutoff param
        assert "2026-04-10" in captured["params"]

    def test_zero_ab_but_walks_only(self):
        # PA from a walk with 0 AB -> avg/slg stay 0 (ab==0) but k_rate/bb_rate set.
        ext = PlatoonFeatureExtractor(MagicMock())
        df = pd.DataFrame(
            [
                {
                    "at_bats": 0,
                    "hits": 0,
                    "total_bases": 0,
                    "walks": 1,
                    "strikeouts": 0,
                    "plate_appearances": 1,
                    "opp_hand": "L",
                },
            ]
        )
        with patch.object(ext, "_safe_query", return_value=df):
            out = ext.extract("Slugger", "2026-04-10", "HITS", pitcher_hand="L")
        assert out["batter_vs_lhp_pa"] == pytest.approx(1.0)
        assert out["batter_vs_lhp_avg"] == pytest.approx(0.0)
        assert out["batter_vs_lhp_bb_rate"] == pytest.approx(1.0)
        assert out["batter_vs_lhp_known"] == 1.0


# =============================================================================
# Opponent lineup splits (STRIKEOUTS)
# =============================================================================
class TestLineupSplits:
    def test_lineup_vs_hand_math(self):
        ext = PlatoonFeatureExtractor(MagicMock())
        df = pd.DataFrame(
            [
                # vs RHP faced: AB 10 H 2 TB 2 BB 0 K 5 PA 10
                {
                    "at_bats": 10,
                    "hits": 2,
                    "total_bases": 2,
                    "walks": 0,
                    "strikeouts": 5,
                    "plate_appearances": 10,
                    "faced_hand": "R",
                },
                # vs LHP faced: AB 10 H 4 TB 8 BB 2 K 1 PA 12
                {
                    "at_bats": 10,
                    "hits": 4,
                    "total_bases": 8,
                    "walks": 2,
                    "strikeouts": 1,
                    "plate_appearances": 12,
                    "faced_hand": "L",
                },
            ]
        )
        with patch.object(ext, "_safe_query", return_value=df):
            out = ext.extract("Acer", "2026-04-10", "STRIKEOUTS", opponent="BOS", pitcher_hand="R")
        assert out["opp_lineup_vs_rhp_k_rate"] == pytest.approx(5 / 10)
        assert out["opp_lineup_vs_rhp_ba"] == pytest.approx(2 / 10)
        assert out["opp_lineup_vs_lhp_k_rate"] == pytest.approx(1 / 12)
        assert out["opp_lineup_vs_lhp_ba"] == pytest.approx(4 / 10)
        # OPS proxy vs LHP: obp (4+2)/(10+2) + slg 8/10
        assert out["opp_lineup_vs_lhp_ops_proxy"] == pytest.approx(6 / 12 + 8 / 10)
        # pitcher is R -> same-hand == vs RHP
        assert out["opp_lineup_same_hand_k_rate"] == pytest.approx(5 / 10)
        assert out["opp_lineup_vs_hand_pa"] == pytest.approx(10.0)
        assert out["opp_lineup_same_hand_known"] == 1.0
        assert out["opp_lineup_platoon_known"] == 1.0

    def test_lineup_no_opponent_no_lookup(self):
        ext = PlatoonFeatureExtractor(MagicMock())
        with patch.object(ext, "_safe_query") as m:
            out = ext.extract("Acer", "2026-04-10", "STRIKEOUTS", pitcher_hand="R")
        m.assert_not_called()
        assert out == PlatoonFeatureExtractor.get_defaults()

    def test_lineup_no_rows_defaults(self):
        ext = PlatoonFeatureExtractor(MagicMock())
        with patch.object(ext, "_safe_query", return_value=None):
            out = ext.extract("Acer", "2026-04-10", "STRIKEOUTS", opponent="BOS", pitcher_hand="R")
        assert out["opp_lineup_platoon_known"] == 0.0
        assert out["opp_lineup_vs_rhp_pa"] == 0.0

    def test_lineup_same_hand_unknown(self):
        ext = PlatoonFeatureExtractor(MagicMock())
        df = pd.DataFrame(
            [
                {
                    "at_bats": 10,
                    "hits": 2,
                    "total_bases": 2,
                    "walks": 0,
                    "strikeouts": 5,
                    "plate_appearances": 10,
                    "faced_hand": "R",
                },
            ]
        )
        with patch.object(ext.hand_resolver, "resolve_hand", return_value=0.0):
            with patch.object(ext, "_safe_query", return_value=df):
                out = ext.extract("Acer", "2026-04-10", "STRIKEOUTS", opponent="BOS")
        assert out["opp_lineup_vs_rhp_known"] == 1.0
        assert out["opp_lineup_same_hand_known"] == 0.0
        assert out["opp_lineup_vs_hand_pa"] == 0.0

    def test_lineup_switch_hand_excluded(self):
        # A faced_hand of 'S'/unknown is dropped from both buckets.
        ext = PlatoonFeatureExtractor(MagicMock())
        df = pd.DataFrame(
            [
                {
                    "at_bats": 5,
                    "hits": 1,
                    "total_bases": 1,
                    "walks": 0,
                    "strikeouts": 3,
                    "plate_appearances": 5,
                    "faced_hand": "R",
                },
                {
                    "at_bats": 5,
                    "hits": 5,
                    "total_bases": 10,
                    "walks": 0,
                    "strikeouts": 0,
                    "plate_appearances": 5,
                    "faced_hand": "S",
                },
            ]
        )
        with patch.object(ext, "_safe_query", return_value=df):
            out = ext.extract("Acer", "2026-04-10", "STRIKEOUTS", opponent="BOS", pitcher_hand="R")
        assert out["opp_lineup_vs_rhp_pa"] == pytest.approx(5.0)
        assert out["opp_lineup_vs_lhp_pa"] == 0.0

    def test_lineup_same_hand_empty_bucket_no_fill(self):
        # own_hand is L, but only RHP-faced rows exist -> same-hand bucket empty
        # (own_hand in buckets, pa == 0, so not known).
        ext = PlatoonFeatureExtractor(MagicMock())
        df = pd.DataFrame(
            [
                {
                    "at_bats": 5,
                    "hits": 1,
                    "total_bases": 1,
                    "walks": 0,
                    "strikeouts": 3,
                    "plate_appearances": 5,
                    "faced_hand": "R",
                },
            ]
        )
        with patch.object(ext, "_safe_query", return_value=df):
            out = ext.extract("Acer", "2026-04-10", "STRIKEOUTS", opponent="BOS", pitcher_hand="L")
        assert out["opp_lineup_vs_hand_pa"] == 0.0
        assert out["opp_lineup_same_hand_known"] == 0.0
        assert out["opp_lineup_vs_rhp_known"] == 1.0

    def test_lineup_zero_ab_walk_only(self):
        # ab == 0 but pa > 0 -> k_rate still set.
        ext = PlatoonFeatureExtractor(MagicMock())
        df = pd.DataFrame(
            [
                {
                    "at_bats": 0,
                    "hits": 0,
                    "total_bases": 0,
                    "walks": 1,
                    "strikeouts": 0,
                    "plate_appearances": 1,
                    "faced_hand": "R",
                },
            ]
        )
        with patch.object(ext, "_safe_query", return_value=df):
            out = ext.extract("Acer", "2026-04-10", "STRIKEOUTS", opponent="BOS", pitcher_hand="R")
        d = PlatoonFeatureExtractor.get_defaults()
        assert out["opp_lineup_vs_rhp_pa"] == pytest.approx(1.0)
        # ab == 0 -> ba/ops stay default
        assert out["opp_lineup_vs_rhp_ba"] == d["opp_lineup_vs_rhp_ba"]
        assert out["opp_lineup_vs_rhp_k_rate"] == pytest.approx(0.0)

    def test_lineup_leakage_cutoff_and_window(self):
        ext = PlatoonFeatureExtractor(MagicMock())
        captured = {}

        def fake_query(query, params, conn=None):
            captured["query"] = query
            captured["params"] = params
            return None

        with patch.object(ext, "_safe_query", side_effect=fake_query):
            ext.extract("Acer", "2026-04-10", "STRIKEOUTS", opponent="BOS", pitcher_hand="R")
        assert "bgl.game_date < %s" in captured["query"]
        assert "INTERVAL '60 days'" in captured["query"]
        assert captured["params"][0] == "BOS"


# =============================================================================
# Routing / misc
# =============================================================================
class TestRouting:
    def test_unknown_stat_returns_defaults(self):
        ext = PlatoonFeatureExtractor(MagicMock())
        with patch.object(ext, "_safe_query") as m:
            out = ext.extract("x", "2026-04-10", "WALKS", opponent="BOS")
        m.assert_not_called()
        assert out == PlatoonFeatureExtractor.get_defaults()

    def test_games_conn_defaults_to_players_conn(self):
        conn = MagicMock()
        ext = PlatoonFeatureExtractor(conn)
        assert ext.games_conn is conn
        assert ext.hand_resolver.conn is conn

    def test_games_conn_separate(self):
        players = MagicMock()
        games = MagicMock()
        ext = PlatoonFeatureExtractor(players, games_conn=games)
        assert ext.games_conn is games
        assert ext.hand_resolver.conn is games

    def test_batter_stat_resolves_hand_via_resolver(self):
        # opp_pitcher_id flows into the resolver; full extract path exercised.
        ext = PlatoonFeatureExtractor(MagicMock())
        with patch.object(ext.hand_resolver, "resolve_hand", return_value=HAND_R) as rh:
            with patch.object(ext, "_safe_query", return_value=None):
                ext.extract(
                    "Slugger",
                    "2026-04-10",
                    "TOTAL_BASES",
                    opponent="BOS",
                    opp_pitcher_id=55,
                    game_id=9,
                )
        rh.assert_called_once()
        assert rh.call_args.kwargs["pitcher_id"] == 55
        assert rh.call_args.kwargs["opponent_team"] == "BOS"
        assert rh.call_args.kwargs["game_id"] == 9

    def test_validate_features_fills_all(self):
        ext = PlatoonFeatureExtractor(MagicMock())
        with patch.object(ext, "_safe_query", return_value=None):
            out = ext.extract("Slugger", "2026-04-10", "TOTAL_BASES", pitcher_hand="L")
        assert set(out.keys()) == set(PlatoonFeatureExtractor.FEATURE_NAMES)
