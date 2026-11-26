"""
Platoon Feature Extractor (MLB)
===============================
Hand-split opponent tendencies and FULL platoon splits.

Separate vs-RHP and vs-LHP splits for a batter, and a lineup's tendencies against each hand,
as a self-contained, leakage-safe extractor.

It produces two families of features:

FULL BATTER PLATOON SPLITS (batter stats: HITS / TOTAL_BASES)
  Computed from the batter's OWN prior game logs, each prior game split by the
  *opposing starter's handedness* in that game (the starter = the opponent-team
  pitcher with the most ``batters_faced`` in that game_id; identical to the
  announced probable pitcher, so known pre-game). For BOTH hands we emit:
      avg, obp_proxy, slg_proxy, ops_proxy, iso_proxy, k_rate, bb_rate
  as ``batter_vs_rhp_*`` and ``batter_vs_lhp_*``. We also emit ``batter_same_hand_*``
  copies selected to match THIS game's opposing-starter hand, plus
  ``batter_vs_rhp_pa`` / ``batter_vs_lhp_pa`` / ``batter_vs_hand_pa`` sample sizes
  and ``batter_platoon_known`` / ``batter_same_hand_known`` flags.

OPPONENT LINEUP TENDENCY vs PITCHER HAND (pitcher stat_type: STRIKEOUTS)
  From the opposing team's batters' prior game logs, each split by the *pitcher
  hand they faced* (resolved the same way). We emit the opponent's K-rate /
  OPS-proxy / BA vs RHP and vs LHP and the value matching THIS pitcher's own
  hand (``opp_lineup_same_hand_*``), with PA sample sizes and ``*_known`` flags.

LEAKAGE: every aggregate derives ONLY from rows with ``game_date`` STRICTLY
before the target game date. The current game's opposing-starter hand is the
announced probable pitcher (pre-game knowledge), never a same-game stat.

MISSINGNESS: no silent constant defaults are baked into the split rates. When a
hand bucket has zero PA, its rate features stay at the neutral ``get_defaults``
value BUT the matching ``*_pa`` count is 0 and ``*_known`` is 0.0, so a caller can tell an
empty bucket from a measured value.
"""

import logging
from typing import Any, Dict, List, Optional, Tuple

from mlb.sql import named_query

from .base import BaseFeatureExtractor

logger = logging.getLogger(__name__)

BATTER_STATS = ("HITS", "TOTAL_BASES")

# Hand codes: R=1, L=2.
HAND_R = 1.0
HAND_L = 2.0

# Per-hand batter rate stats emitted for each of {rhp, lhp, same_hand}.
_BATTER_RATE_STATS = (
    "avg",
    "obp_proxy",
    "slg_proxy",
    "ops_proxy",
    "iso_proxy",
    "k_rate",
    "bb_rate",
)

# Per-hand opponent-lineup rate stats emitted for each of {rhp, lhp, same_hand}.
_LINEUP_RATE_STATS = ("k_rate", "ops_proxy", "ba")


def _build_batter_feature_names() -> Tuple[str, ...]:
    names: List[str] = []
    for hand in ("rhp", "lhp", "same_hand"):
        for stat in _BATTER_RATE_STATS:
            names.append(
                f"batter_vs_{hand}_{stat}" if hand != "same_hand" else f"batter_same_hand_{stat}"
            )
    names.extend(
        [
            "batter_vs_rhp_pa",
            "batter_vs_lhp_pa",
            "batter_vs_hand_pa",
            "batter_vs_rhp_known",
            "batter_vs_lhp_known",
            "batter_same_hand_known",
            "batter_platoon_known",
        ]
    )
    return tuple(names)


def _build_lineup_feature_names() -> Tuple[str, ...]:
    names: List[str] = []
    for hand in ("rhp", "lhp", "same_hand"):
        for stat in _LINEUP_RATE_STATS:
            prefix = "opp_lineup_vs" if hand != "same_hand" else "opp_lineup_same_hand"
            names.append(f"{prefix}_{hand}_{stat}" if hand != "same_hand" else f"{prefix}_{stat}")
    names.extend(
        [
            "opp_lineup_vs_rhp_pa",
            "opp_lineup_vs_lhp_pa",
            "opp_lineup_vs_hand_pa",
            "opp_lineup_vs_rhp_known",
            "opp_lineup_vs_lhp_known",
            "opp_lineup_same_hand_known",
            "opp_lineup_platoon_known",
        ]
    )
    return tuple(names)


class OpposingStarterHandResolver:
    """Resolve a single game's opposing-starter throwing hand.

    Reusable, leakage-safe helper. Two lookup strategies, in priority order:

    1. ``probable_pitchers`` (the announced starter for the opponent team in a
       game) joined to ``pitcher_profile.throws`` - used for the CURRENT game's
       opposing starter when an id/name isn't already supplied.
    2. ``pitcher_profile`` directly by id or name - used when the caller already
       knows which pitcher is starting.

    Returns a hand code (R=1.0, L=2.0) or 0.0 when unknown. Pure DB reads of
    reference/schedule rows; no aggregation of in-game stats, so it carries no
    leakage on its own.
    """

    def __init__(self, conn: Any):
        self.conn = conn

    @staticmethod
    def encode_hand(value: Any) -> float:
        """Encode throwing hand: R=1.0, L=2.0, else (S/None/'') 0.0."""
        if not value:
            return 0.0
        v = str(value).strip().upper()
        if v.startswith("R"):
            return HAND_R
        if v.startswith("L"):
            return HAND_L
        return 0.0

    def _query(self, query: str, params: tuple):
        import pandas as pd

        try:
            df = pd.read_sql_query(query, self.conn, params=params)
            return df if len(df) > 0 else None
        except (pd.errors.DatabaseError, ValueError, TypeError) as exc:
            logger.debug("OpposingStarterHandResolver query failed: %s", exc)
            return None

    def resolve_throws(
        self,
        pitcher_id: Optional[int] = None,
        pitcher_name: Optional[str] = None,
    ) -> Optional[str]:
        """Return the raw ``throws`` ('R'/'L'/None) for a pitcher by id or name."""
        if pitcher_id is not None:
            df = self._query(
                "SELECT throws FROM pitcher_profile WHERE player_id = %s LIMIT 1",
                (pitcher_id,),
            )
        elif pitcher_name:
            df = self._query(
                "SELECT throws FROM pitcher_profile "
                "WHERE unaccent(full_name) = unaccent(%s) LIMIT 1",
                (pitcher_name,),
            )
        else:
            return None
        if df is None or len(df) == 0:
            return None
        val = df["throws"].iloc[0]
        return None if val is None or val != val else str(val)

    def resolve_from_schedule(
        self,
        game_id: Optional[int],
        opponent_team: Optional[str],
        game_date: Optional[str] = None,
    ) -> Optional[str]:
        """Resolve the opponent team's probable starter ``throws`` for a game.

        Joins ``probable_pitchers`` -> ``pitcher_profile``. ``game_id`` +
        ``opponent_team`` is the precise key; ``game_date`` + ``opponent_team``
        is a fallback when only the date is known.
        """
        if opponent_team and game_id is not None:
            df = self._query(
                """
                SELECT pp.throws
                FROM mlb_games.probable_pitchers ppk
                JOIN mlb_players.pitcher_profile pp ON ppk.pitcher_id = pp.player_id
                WHERE ppk.game_id = %s AND ppk.team = %s
                LIMIT 1
                """,
                (game_id, opponent_team),
            )
        elif opponent_team and game_date:
            df = self._query(
                """
                SELECT pp.throws
                FROM mlb_games.probable_pitchers ppk
                JOIN mlb_players.pitcher_profile pp ON ppk.pitcher_id = pp.player_id
                WHERE ppk.game_date = %s AND ppk.team = %s
                LIMIT 1
                """,
                (game_date, opponent_team),
            )
        else:
            return None
        if df is None or len(df) == 0:
            return None
        val = df["throws"].iloc[0]
        return None if val is None or val != val else str(val)

    def resolve_hand(
        self,
        pitcher_id: Optional[int] = None,
        pitcher_name: Optional[str] = None,
        pitcher_hand: Optional[str] = None,
        game_id: Optional[int] = None,
        opponent_team: Optional[str] = None,
        game_date: Optional[str] = None,
    ) -> float:
        """Resolve the opposing-starter hand code, trying each source in order.

        Priority: explicit ``pitcher_hand`` > id/name profile lookup > schedule
        (probable_pitchers) lookup. Returns 0.0 if nothing resolves.
        """
        if pitcher_hand:
            return self.encode_hand(pitcher_hand)
        throws = self.resolve_throws(pitcher_id=pitcher_id, pitcher_name=pitcher_name)
        if throws is None:
            throws = self.resolve_from_schedule(game_id, opponent_team, game_date)
        return self.encode_hand(throws)


class PlatoonFeatureExtractor(BaseFeatureExtractor):
    """Full hand-split batter platoon splits + opponent-lineup-vs-hand tendencies."""

    FEATURE_NAMES = _build_batter_feature_names() + _build_lineup_feature_names()

    def __init__(self, players_conn: Any, games_conn: Any = None):
        """
        Args:
            players_conn: Connection to ``mlb_players`` (game logs + profiles). The one
                query that also reads ``mlb_games`` (probable_pitchers) names both schemas,
                because a connection's search path covers only its own.
            games_conn: Optional connection for the schedule (probable_pitchers)
                lookup used by the hand resolver. Defaults to ``players_conn``.
        """
        super().__init__(players_conn, name="PlatoonFeatures")
        self.games_conn = games_conn if games_conn is not None else players_conn
        self.hand_resolver = OpposingStarterHandResolver(self.games_conn)

    @classmethod
    def get_defaults(cls) -> Dict[str, float]:
        """Neutral defaults. Rate stats use league-ish neutral values, but the
        accompanying ``*_pa`` / ``*_known`` flags reveal when a bucket is empty
        so a caller can tell an empty bucket from a measured value.
        """
        defaults: Dict[str, float] = {}
        # Neutral batter rate stats per hand.
        neutral_batter = {
            "avg": 0.250,
            "obp_proxy": 0.320,
            "slg_proxy": 0.400,
            "ops_proxy": 0.720,
            "iso_proxy": 0.150,
            "k_rate": 0.220,
            "bb_rate": 0.080,
        }
        for hand in ("rhp", "lhp"):
            for stat, val in neutral_batter.items():
                defaults[f"batter_vs_{hand}_{stat}"] = val
        for stat, val in neutral_batter.items():
            defaults[f"batter_same_hand_{stat}"] = val
        defaults.update(
            {
                "batter_vs_rhp_pa": 0.0,
                "batter_vs_lhp_pa": 0.0,
                "batter_vs_hand_pa": 0.0,
                "batter_vs_rhp_known": 0.0,
                "batter_vs_lhp_known": 0.0,
                "batter_same_hand_known": 0.0,
                "batter_platoon_known": 0.0,
            }
        )
        # Neutral opponent-lineup rate stats per hand.
        neutral_lineup = {"k_rate": 0.220, "ops_proxy": 0.720, "ba": 0.250}
        for hand in ("rhp", "lhp"):
            for stat, val in neutral_lineup.items():
                defaults[f"opp_lineup_vs_{hand}_{stat}"] = val
        for stat, val in neutral_lineup.items():
            defaults[f"opp_lineup_same_hand_{stat}"] = val
        defaults.update(
            {
                "opp_lineup_vs_rhp_pa": 0.0,
                "opp_lineup_vs_lhp_pa": 0.0,
                "opp_lineup_vs_hand_pa": 0.0,
                "opp_lineup_vs_rhp_known": 0.0,
                "opp_lineup_vs_lhp_known": 0.0,
                "opp_lineup_same_hand_known": 0.0,
                "opp_lineup_platoon_known": 0.0,
            }
        )
        return defaults

    # ==================================================================
    # LIVE (DB-query) PATH
    # ==================================================================
    def extract(
        self,
        player_name: str,
        game_date: Any,
        stat_type: str = "HITS",
        opponent: str = None,
        probable_pitcher: str = None,
        pitcher_hand: str = None,
        opp_pitcher_id: int = None,
        game_id: int = None,
        **kwargs,
    ) -> Dict[str, float]:
        """Extract platoon features for one player-game.

        Args:
            player_name: Batter (batter stats) or pitcher (STRIKEOUTS) name.
            game_date: Target game date (exclusive leakage cutoff).
            stat_type: HITS / TOTAL_BASES (batter) or STRIKEOUTS (pitcher).
            opponent: Opposing team abbreviation.
            probable_pitcher: Opposing starter name (batter stats).
            pitcher_hand: This game's pitcher hand if already known.
                - batter stats: the OPPOSING starter's hand.
                - STRIKEOUTS: the target pitcher's OWN hand.
            opp_pitcher_id: Opposing starter player_id (batter stats, preferred).
            game_id: Current game id (used by schedule hand resolution).
        """
        game_date_str = self._normalize_date(game_date)
        features = self.get_defaults()
        stat_type = stat_type.upper()

        if stat_type in BATTER_STATS:
            opp_hand = self.hand_resolver.resolve_hand(
                pitcher_id=opp_pitcher_id,
                pitcher_name=probable_pitcher,
                pitcher_hand=pitcher_hand,
                game_id=game_id,
                opponent_team=opponent,
                game_date=game_date_str,
            )
            self._batter_full_splits(player_name, opp_hand, game_date_str, features)
        elif stat_type == "STRIKEOUTS" and opponent:
            # For strikeouts the relevant hand is the target pitcher's OWN hand.
            own_hand = self.hand_resolver.resolve_hand(
                pitcher_id=opp_pitcher_id,
                pitcher_name=player_name,
                pitcher_hand=pitcher_hand,
            )
            self._opp_lineup_splits(opponent, own_hand, game_date_str, features)

        return self.validate_features(features)

    def _batter_full_splits(
        self,
        batter_name: str,
        opp_hand: float,
        game_date_str: str,
        features: Dict[str, float],
    ) -> None:
        """Pull the batter's prior logs joined to each game's opposing-starter
        hand, then compute full vs-RHP and vs-LHP splits + the same-hand copy.
        """
        query = named_query("batter_games_with_opposing_hand")
        df = self._safe_query(query, (batter_name, game_date_str))
        if df is None or len(df) == 0:
            return
        self._apply_batter_full_splits(df.to_dict("records"), opp_hand, features)

    def _opp_lineup_splits(
        self,
        opponent: str,
        own_hand: float,
        game_date_str: str,
        features: Dict[str, float],
    ) -> None:
        """Opponent lineup's hand-split tendency: the opposing team's batters'
        prior logs split by the pitcher hand they faced.
        """
        query = named_query("lineup_games_with_faced_hand")
        df = self._safe_query(query, (opponent, game_date_str, game_date_str))
        if df is None or len(df) == 0:
            return
        self._apply_lineup_splits(df.to_dict("records"), own_hand, features)

    # ==================================================================
    # SHARED COMPUTE HELPERS
    # ==================================================================
    @classmethod
    def _apply_batter_full_splits(
        cls, games: List[Dict], opp_hand: float, features: Dict[str, float]
    ) -> None:
        """Compute full vs-RHP / vs-LHP splits + same-hand copy from per-game dicts.

        ``games`` rows carry at_bats/hits/total_bases/walks/strikeouts/
        plate_appearances and an ``opp_hand`` field (the throwing-hand of THAT
        game's opposing starter, 'R'/'L'/None).
        """
        buckets = {HAND_R: cls._new_bucket(), HAND_L: cls._new_bucket()}
        for g in games:
            code = OpposingStarterHandResolver.encode_hand(g.get("opp_hand"))
            if code not in buckets:
                continue  # switch/unknown starter hand -> excluded
            cls._accumulate(buckets[code], g)

        any_known = False
        for code, tag in ((HAND_R, "rhp"), (HAND_L, "lhp")):
            b = buckets[code]
            features[f"batter_vs_{tag}_pa"] = b["pa"]
            if b["pa"] > 0:
                any_known = True
                features[f"batter_vs_{tag}_known"] = 1.0
                cls._fill_batter_rates(b, f"batter_vs_{tag}_", features)
        features["batter_platoon_known"] = 1.0 if any_known else 0.0

        if opp_hand in buckets:
            same = buckets[opp_hand]
            features["batter_vs_hand_pa"] = same["pa"]
            if same["pa"] > 0:
                features["batter_same_hand_known"] = 1.0
                cls._fill_batter_rates(same, "batter_same_hand_", features)

    @classmethod
    def _apply_lineup_splits(
        cls, games: List[Dict], own_hand: float, features: Dict[str, float]
    ) -> None:
        """Compute opponent-lineup K-rate/OPS-proxy/BA vs each pitcher hand."""
        buckets = {HAND_R: cls._new_bucket(), HAND_L: cls._new_bucket()}
        for g in games:
            code = OpposingStarterHandResolver.encode_hand(g.get("faced_hand"))
            if code not in buckets:
                continue
            cls._accumulate(buckets[code], g)

        any_known = False
        for code, tag in ((HAND_R, "rhp"), (HAND_L, "lhp")):
            b = buckets[code]
            features[f"opp_lineup_vs_{tag}_pa"] = b["pa"]
            if b["pa"] > 0:
                any_known = True
                features[f"opp_lineup_vs_{tag}_known"] = 1.0
                cls._fill_lineup_rates(b, f"opp_lineup_vs_{tag}_", features)
        features["opp_lineup_platoon_known"] = 1.0 if any_known else 0.0

        if own_hand in buckets:
            same = buckets[own_hand]
            features["opp_lineup_vs_hand_pa"] = same["pa"]
            if same["pa"] > 0:
                features["opp_lineup_same_hand_known"] = 1.0
                cls._fill_lineup_rates(same, "opp_lineup_same_hand_", features)

    # ---- bucket math -------------------------------------------------
    @staticmethod
    def _new_bucket() -> Dict[str, float]:
        return {"ab": 0.0, "h": 0.0, "tb": 0.0, "bb": 0.0, "k": 0.0, "pa": 0.0}

    @staticmethod
    def _accumulate(bucket: Dict[str, float], g: Dict) -> None:
        bucket["ab"] += float(g.get("at_bats") or 0)
        bucket["h"] += float(g.get("hits") or 0)
        bucket["tb"] += float(g.get("total_bases") or 0)
        bucket["bb"] += float(g.get("walks") or 0)
        bucket["k"] += float(g.get("strikeouts") or 0)
        bucket["pa"] += float(g.get("plate_appearances") or 0)

    @classmethod
    def _fill_batter_rates(
        cls, b: Dict[str, float], prefix: str, features: Dict[str, float]
    ) -> None:
        ab, h, tb, bb, k, pa = b["ab"], b["h"], b["tb"], b["bb"], b["k"], b["pa"]
        avg = h / ab if ab > 0 else 0.0
        slg = tb / ab if ab > 0 else 0.0
        iso = (tb - h) / ab if ab > 0 else 0.0
        denom = ab + bb
        obp = (h + bb) / denom if denom > 0 else 0.0
        features[f"{prefix}avg"] = avg
        features[f"{prefix}slg_proxy"] = slg
        features[f"{prefix}iso_proxy"] = iso
        features[f"{prefix}obp_proxy"] = obp
        features[f"{prefix}ops_proxy"] = obp + slg
        # pa is always > 0 here (callers gate on a positive PA bucket).
        features[f"{prefix}k_rate"] = k / pa
        features[f"{prefix}bb_rate"] = bb / pa

    @classmethod
    def _fill_lineup_rates(
        cls, b: Dict[str, float], prefix: str, features: Dict[str, float]
    ) -> None:
        # pa is always > 0 here (callers gate on a positive PA bucket).
        ab, h, tb, bb, k, pa = b["ab"], b["h"], b["tb"], b["bb"], b["k"], b["pa"]
        if ab > 0:
            features[f"{prefix}ba"] = h / ab
            slg = tb / ab
            denom = ab + bb
            obp = (h + bb) / denom if denom > 0 else 0.0
            features[f"{prefix}ops_proxy"] = obp + slg
        features[f"{prefix}k_rate"] = k / pa
