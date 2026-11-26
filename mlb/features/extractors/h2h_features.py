"""
Head-to-Head (BvP) Feature Extractor (MLB)
==========================================
Extracts batter-vs-pitcher (BvP) history for a (batter, opposing starter)
pair, reconstructed "as-of" from per-encounter rows in
``mlb_players.bvp_encounters`` (populated by
``mlb.scripts.loaders.load_statcast_pitchmix`` from raw Baseball Savant
pitches).

These are the plate-appearance outcomes between this batter and this pitcher, not a
platoon-split proxy.

LEAKAGE-SAFE AS-OF RECONSTRUCTION
---------------------------------
``bvp_encounters`` is stored at per-(batter, pitcher, GAME) granularity with a
``game_date``. The cumulative (batter, pitcher) ``matchup_history`` rollup is
NOT used here because it would mix in encounters from the target game's own date and
later. Instead the extractor SUMS only encounter rows with
``game_date < target_date`` (strictly before), so BvP reflects exactly what was
knowable before first pitch. The target game's own outcome never leaks in.

EXPLICIT MISSINGNESS (no silent default)
----------------------------------------
When a pair has no prior encounters, league-ish defaults are returned BUT
``bvp_real_pa`` = 0 and ``bvp_known`` = 0 make the absence explicit, so a caller can tell a
pair with no history from a measured value.
"""

import logging
from typing import Any, Dict

from .base import BaseFeatureExtractor

logger = logging.getLogger(__name__)


class H2HFeatureExtractor(BaseFeatureExtractor):
    """Real batter-vs-pitcher (BvP) features, reconstructed as-of game_date."""

    FEATURE_NAMES = (
        "bvp_real_pa",  # count of prior plate appearances vs this pitcher (explicit missingness)
        "bvp_known",  # 1.0 when >=1 prior PA exists, else 0.0
        "bvp_avg",  # hits / at-bats (PA - BB)
        "bvp_k_rate",  # K / PA
        "bvp_ops_proxy",  # (hits+bb)/PA + tb/PA  (OBP-proxy + SLG-proxy)
    )

    # League-ish neutral defaults (returned WITH bvp_known=0 when no history).
    _DEFAULTS = {
        "bvp_real_pa": 0.0,
        "bvp_known": 0.0,
        "bvp_avg": 0.245,
        "bvp_k_rate": 0.22,
        "bvp_ops_proxy": 0.715,
    }

    def __init__(self, conn: Any):
        """
        Args:
            conn: Connection to the ``mlb_players`` schema (bvp_encounters +
                  batter_profile / pitcher_profile for name resolution).
        """
        super().__init__(conn, name="H2HFeatures")

    @classmethod
    def get_defaults(cls) -> Dict[str, float]:
        return dict(cls._DEFAULTS)

    def extract(
        self,
        player_name: str,
        game_date: Any,
        stat_type: str = "HITS",
        **kwargs,
    ) -> Dict[str, float]:
        """
        Extract real BvP features for a (batter, opposing starter) as of date.

        Resolution prefers explicit IDs (Statcast ids == our player_id):
            ``batter_id`` (or ``player_id``) and ``pitcher_id``
            (or ``opp_pitcher_id``). Falls back to name lookups via
            ``opp_pitcher_name`` for the pitcher when only the batter name is
            known.

        Args:
            player_name: Batter full name.
            game_date: Target game date (leakage cutoff, exclusive).
            stat_type: Batter stat type (unused for routing; BvP is stat-agnostic).
            **kwargs: batter_id / player_id, pitcher_id / opp_pitcher_id,
                      opp_pitcher_name.

        Returns:
            Dict of BvP features. When no prior encounter exists, defaults with
            ``bvp_known`` = 0 and ``bvp_real_pa`` = 0 (explicit missingness).
        """
        game_date_str = self._normalize_date(game_date)

        batter_id = kwargs.get("batter_id", kwargs.get("player_id"))
        pitcher_id = kwargs.get("pitcher_id", kwargs.get("opp_pitcher_id"))
        opp_pitcher_name = kwargs.get("opp_pitcher_name")

        df = self._query_encounters(
            batter_id, pitcher_id, player_name, opp_pitcher_name, game_date_str
        )
        if df is None or len(df) == 0:
            return self.get_defaults()

        return self._compute_features(df)

    # ------------------------------------------------------------------

    def _query_encounters(
        self,
        batter_id,
        pitcher_id,
        batter_name,
        pitcher_name,
        game_date_str: str,
    ):
        """
        Pull strictly-before encounter rows for the pair. Prefers ID join; uses
        unaccent name joins to resolve whichever id(s) are missing.
        """
        select = "SELECT be.pa, be.hits, be.k, be.bb, be.tb"
        where = ["be.game_date < %s"]
        joins = ""
        params = [game_date_str]

        if batter_id is not None:
            where.append("be.batter_id = %s")
            params.append(batter_id)
        else:
            joins += " JOIN batter_profile bp ON be.batter_id = bp.player_id"
            where.append("unaccent(bp.full_name) = unaccent(%s)")
            params.append(batter_name)

        if pitcher_id is not None:
            where.append("be.pitcher_id = %s")
            params.append(pitcher_id)
        elif pitcher_name is not None:
            joins += " JOIN pitcher_profile pp ON be.pitcher_id = pp.player_id"
            where.append("unaccent(pp.full_name) = unaccent(%s)")
            params.append(pitcher_name)
        else:
            # No way to identify the opposing pitcher -> no real BvP.
            return None

        query = f"{select} FROM bvp_encounters be{joins} WHERE " + " AND ".join(where)
        return self._safe_query(query, tuple(params))

    def _compute_features(self, df) -> Dict[str, float]:
        """Sum the as-of encounter rows into cumulative BvP rate features."""
        features = self.get_defaults()

        def _col_sum(col: str) -> int:
            return int(sum(int(v) for v in df[col].fillna(0).tolist()))

        pa = _col_sum("pa")
        if pa <= 0:
            return features  # known=0, defaults retained

        hits = _col_sum("hits")
        k = _col_sum("k")
        bb = _col_sum("bb")
        tb = _col_sum("tb")
        ab = max(pa - bb, 0)

        features["bvp_real_pa"] = float(pa)
        features["bvp_known"] = 1.0
        features["bvp_k_rate"] = k / pa
        features["bvp_avg"] = (hits / ab) if ab > 0 else 0.0
        features["bvp_ops_proxy"] = (hits + bb) / pa + tb / pa

        return features
