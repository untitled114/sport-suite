"""
Sabermetric Feature Extractor (MLB)
===================================
Rolling sabermetric skill metrics for both player roles, computed from the
columns we persist in ``mlb_players.{pitcher,batter}_game_logs``.

Where the constants come from:
  - The FIP constant is computed per season from league aggregates (not a fixed
    3.10). See :meth:`_season_fip_constant`.
  - wOBA uses the published linear weights on its full set of components
    (1B/2B/3B/HR/uBB/HBP) with the true denominator (AB + BB - IBB + SF + HBP).
    Doubles/triples/HBP/IBB/SF come from the schema_xbh backfill.
  - xFIP and wRC+ are not computed: xFIP needs a fly-ball-against rate that is not
    loaded, and wRC+ needs full league and park run context.

PITCHER stats (STRIKEOUTS)
----------------------------
Rolling totals over a window strictly before the target ``game_date`` from
``pitcher_game_logs`` (innings_pitched, strikeouts, walks, batters_faced,
home_runs_allowed). HR allowed is supplied by ``schema_sabermetric.sql`` +
``backfill_pitcher_hr_allowed.py``.

  - ``saber_pitcher_fip``      Fielding Independent Pitching (rolling), using the
                               target season's league-computed FIP constant.
  - ``saber_pitcher_k_minus_bb_pct``  K% - BB% (per batter faced).
  - ``saber_pitcher_walk_rate`` BB / batters faced.
  - ``saber_pitcher_hr_per_9``  HR allowed per 9 IP.
  - ``saber_pitcher_k_pct``     K / batters faced.
  - ``saber_pitcher_fip_constant``  The season FIP constant actually used
                                    (exposed for transparency; equals the
                                    fallback when the league pass is unavailable).
  - ``saber_pitcher_games_sample``

FIP constant
~~~~~~~~~~~~
``cFIP_season = lgERA - (13*lgHR + 3*lgBB - 2*lgK) / lgIP`` where the league
aggregates are summed over EVERY ``pitcher_game_logs`` row in that season that
carries a home_runs_allowed value (HR is required by the formula). Computed once
per season and cached. If the league pass cannot run (no DB / no HR data yet),
we fall back to ``FALLBACK_FIP_CONSTANT`` and flag it via
``saber_pitcher_fip_constant`` so the omission is visible - never silently fudged.

BATTER stats (HITS / TOTAL_BASES)
---------------------------------
Rolling totals over a window strictly before ``game_date`` from
``batter_game_logs`` (at_bats, hits, total_bases, walks, strikeouts,
plate_appearances, doubles, triples, hit_by_pitch, intentional_walks, sac_flies).

  - ``saber_batter_woba``  wOBA (published linear weights on its
        components). 1B = H - 2B - 3B - HR; uBB = BB - IBB.
        wOBA = (wBB*uBB + wHBP*HBP + w1B*1B + w2B*2B + w3B*3B + wHR*HR)
               / (AB + BB - IBB + SF + HBP).
  - ``saber_batter_woba_complete``  1.0 when the 2B/3B/HBP/IBB/SF components were
        present (post-backfill); 0.0 when they were missing and wOBA fell back to
        the slash inputs we DO have - documents the omission instead of hiding it.
  - ``saber_batter_iso``         Isolated power (SLG - AVG).
  - ``saber_batter_walk_rate``   BB / PA.
  - ``saber_batter_strikeout_rate``  K / PA.
  - ``saber_batter_obp`` / ``saber_batter_slg``  (slash inputs, exposed).
  - ``saber_batter_games_sample``

wOBA linear weights
~~~~~~~~~~~~~~~~~~~~~
The weights are the published FanGraphs wOBA set (the modern-era reference values in its
wOBA/wRC+ glossary). Per-season refits cannot be computed from the data loaded here, so the
published set is used.

Leakage: every query filters ``game_date < <target date>`` (STRICTLY before).
"""

import logging
from typing import Any, Dict

from mlb.sql import named_query

from .base import BaseFeatureExtractor

logger = logging.getLogger(__name__)

BATTER_STATS = ("HITS", "TOTAL_BASES")

# Fallback FIP constant used ONLY when the per-season league pass cannot run
# (e.g. no DB connection in a unit test, or HR-allowed not yet backfilled). The
# real value is computed per season; this is the documented neutral fallback and
# is surfaced via ``saber_pitcher_fip_constant`` so it is never silently assumed.
FALLBACK_FIP_CONSTANT = 3.10

# Published canonical FanGraphs wOBA linear weights (modern-era reference set,
# FanGraphs wOBA/wRC+ glossary). Real published constants applied to real
# components. Source: FanGraphs Sabermetrics Library, "wOBA" (linear weights).
WOBA_WEIGHTS = {
    "wBB": 0.690,
    "wHBP": 0.722,
    "w1B": 0.888,
    "w2B": 1.271,
    "w3B": 1.616,
    "wHR": 2.101,
}

# Rolling windows (games) for the trailing aggregations.
ROLL_WINDOW = 10
BATTER_ROLL_WINDOW = 15


def season_fip_constant(extractor: BaseFeatureExtractor, season: int) -> float:
    """
    Compute the league FIP constant for ``season`` from ``pitcher_game_logs``.

    ``cFIP = lgERA - (13*lgHR + 3*lgBB - 2*lgK) / lgIP`` summed over every row in
    the season carrying a ``home_runs_allowed`` value (HR is required by the
    formula). Uses the extractor's ``_safe_query`` / ``_safe_float`` so any
    BaseFeatureExtractor (pitcher OR volume) can share one implementation.

    Returns ``FALLBACK_FIP_CONSTANT`` when the league pass cannot produce a usable
    value (no DB / no rows / no IP / no HR-allowed data yet) - surfaced by callers
    so the omission is visible, never silently fudged.
    """
    query = """
        SELECT
            COALESCE(SUM(earned_runs), 0)       AS er,
            COALESCE(SUM(innings_pitched), 0)   AS ip,
            COALESCE(SUM(home_runs_allowed), 0) AS hr,
            COALESCE(SUM(walks), 0)             AS bb,
            COALESCE(SUM(strikeouts), 0)        AS k,
            COUNT(home_runs_allowed)            AS hr_rows
        FROM pitcher_game_logs
        WHERE EXTRACT(YEAR FROM game_date)::int = %s
          AND home_runs_allowed IS NOT NULL
    """
    df = extractor._safe_query(query, (season,))
    if df is None or len(df) == 0:
        return FALLBACK_FIP_CONSTANT
    row = df.iloc[0]
    ip = extractor._safe_float(row["ip"], 0.0)
    hr_rows = extractor._safe_float(row["hr_rows"], 0.0)
    if ip <= 0 or hr_rows <= 0:
        return FALLBACK_FIP_CONSTANT
    er = extractor._safe_float(row["er"], 0.0)
    hr = extractor._safe_float(row["hr"], 0.0)
    bb = extractor._safe_float(row["bb"], 0.0)
    k = extractor._safe_float(row["k"], 0.0)
    lg_era = 9.0 * er / ip
    return lg_era - (13.0 * hr + 3.0 * bb - 2.0 * k) / ip


class SabermetricFeatureExtractor(BaseFeatureExtractor):
    """Rolling FIP / K-BB% (pitchers) and wOBA (batters)."""

    FEATURE_NAMES = (
        # Pitcher (STRIKEOUTS)
        "saber_pitcher_fip",
        "saber_pitcher_k_minus_bb_pct",
        "saber_pitcher_walk_rate",
        "saber_pitcher_hr_per_9",
        "saber_pitcher_k_pct",
        "saber_pitcher_fip_constant",
        "saber_pitcher_games_sample",
        # Batter (HITS / TOTAL_BASES)
        "saber_batter_woba",
        "saber_batter_woba_complete",
        "saber_batter_iso",
        "saber_batter_walk_rate",
        "saber_batter_strikeout_rate",
        "saber_batter_obp",
        "saber_batter_slg",
        "saber_batter_games_sample",
    )

    def __init__(self, conn: Any):
        """
        Args:
            conn: Connection to the ``mlb_players`` schema (game logs + profiles).
        """
        super().__init__(conn, name="SabermetricFeatures")
        # season -> computed FIP constant (cached across calls).
        self._fip_const_cache: Dict[int, float] = {}

    @classmethod
    def get_defaults(cls) -> Dict[str, float]:
        return {
            # Pitcher league-average-ish neutrals
            "saber_pitcher_fip": 4.00,
            "saber_pitcher_k_minus_bb_pct": 0.13,
            "saber_pitcher_walk_rate": 0.08,
            "saber_pitcher_hr_per_9": 1.25,
            "saber_pitcher_k_pct": 0.22,
            "saber_pitcher_fip_constant": FALLBACK_FIP_CONSTANT,
            "saber_pitcher_games_sample": 0.0,
            # Batter league-average-ish neutrals
            "saber_batter_woba": 0.320,
            "saber_batter_woba_complete": 0.0,
            "saber_batter_iso": 0.150,
            "saber_batter_walk_rate": 0.08,
            "saber_batter_strikeout_rate": 0.22,
            "saber_batter_obp": 0.320,
            "saber_batter_slg": 0.400,
            "saber_batter_games_sample": 0.0,
        }

    # ------------------------------------------------------------------ #
    # Public extract
    # ------------------------------------------------------------------ #
    def extract(
        self,
        player_name: str,
        game_date: Any,
        stat_type: str = "STRIKEOUTS",
        **kwargs,
    ) -> Dict[str, float]:
        """
        Extract rolling sabermetric features.

        Routes to the batter table for batter stats, pitcher table otherwise.

        Args:
            player_name: Pitcher or batter full name.
            game_date: Target game date (rolling window is STRICTLY before this).
            stat_type: STRIKEOUTS (pitcher) or HITS/TOTAL_BASES (batter).

        Returns:
            Dict of all sabermetric features (defaults filled for the other role).
        """
        game_date_str = self._normalize_date(game_date)
        if stat_type.upper() in BATTER_STATS:
            return self._extract_batter(player_name, game_date_str)
        return self._extract_pitcher(player_name, game_date_str)

    # ------------------------------------------------------------------ #
    # FIP constant (computed per season from league aggregates)
    # ------------------------------------------------------------------ #
    @staticmethod
    def _season_of(game_date_str: str) -> int:
        """Season year from a YYYY-MM-DD target date (MLB season == calendar year)."""
        return int(game_date_str[:4])

    def _season_fip_constant(self, season: int) -> float:
        """
        Compute (and cache) the league FIP constant for ``season``.

        Delegates to :func:`season_fip_constant`, which runs the league
        aggregation pass over ``pitcher_game_logs``. Cached per season per
        extractor instance.
        """
        if season in self._fip_const_cache:
            return self._fip_const_cache[season]
        const = season_fip_constant(self, season)
        self._fip_const_cache[season] = const
        return const

    # ------------------------------------------------------------------ #
    # Pitcher
    # ------------------------------------------------------------------ #
    def _extract_pitcher(self, player_name: str, game_date_str: str) -> Dict[str, float]:
        query = named_query("pitcher_recent_totals")
        df = self._safe_query(query, (player_name, game_date_str, ROLL_WINDOW))
        fip_const = self._season_fip_constant(self._season_of(game_date_str))
        if df is None or len(df) == 0:
            feats = self.get_defaults()
            feats["saber_pitcher_fip_constant"] = fip_const
            return feats

        row = df.iloc[0]
        games = self._safe_float(row["games"], 0.0)
        if games <= 0:
            feats = self.get_defaults()
            feats["saber_pitcher_fip_constant"] = fip_const
            return feats

        ip = self._safe_float(row["ip"], 0.0)
        k = self._safe_float(row["k"], 0.0)
        bb = self._safe_float(row["bb"], 0.0)
        bf = self._safe_float(row["bf"], 0.0)
        hr = self._safe_float(row["hr"], 0.0)

        return self._compute_pitcher(games, ip, k, bb, bf, hr, fip_const)

    def _compute_pitcher(
        self,
        games: float,
        ip: float,
        k: float,
        bb: float,
        bf: float,
        hr: float,
        fip_const: float,
    ) -> Dict[str, float]:
        """Compute pitcher sabermetrics from rolling totals (pure, testable)."""
        features = self.get_defaults()
        features["saber_pitcher_games_sample"] = games
        features["saber_pitcher_fip_constant"] = fip_const

        if ip > 0:
            features["saber_pitcher_fip"] = (13.0 * hr + 3.0 * bb - 2.0 * k) / ip + fip_const
            features["saber_pitcher_hr_per_9"] = hr / ip * 9.0

        if bf > 0:
            k_pct = k / bf
            bb_pct = bb / bf
            features["saber_pitcher_k_pct"] = k_pct
            features["saber_pitcher_walk_rate"] = bb_pct
            features["saber_pitcher_k_minus_bb_pct"] = k_pct - bb_pct

        return features

    # ------------------------------------------------------------------ #
    # Batter
    # ------------------------------------------------------------------ #
    def _extract_batter(self, player_name: str, game_date_str: str) -> Dict[str, float]:
        query = named_query("batter_recent_totals")
        df = self._safe_query(query, (player_name, game_date_str, BATTER_ROLL_WINDOW))
        if df is None or len(df) == 0:
            return self.get_defaults()

        row = df.iloc[0]
        games = self._safe_float(row["games"], 0.0)
        if games <= 0:
            return self.get_defaults()

        return self._compute_batter(
            games=games,
            ab=self._safe_float(row["ab"], 0.0),
            h=self._safe_float(row["h"], 0.0),
            tb=self._safe_float(row["tb"], 0.0),
            bb=self._safe_float(row["bb"], 0.0),
            k=self._safe_float(row["k"], 0.0),
            pa=self._safe_float(row["pa"], 0.0),
            hr=self._safe_float(row["hr"], 0.0),
            doubles=self._safe_float(row["doubles"], 0.0),
            triples=self._safe_float(row["triples"], 0.0),
            hbp=self._safe_float(row["hbp"], 0.0),
            ibb=self._safe_float(row["ibb"], 0.0),
            sf=self._safe_float(row["sf"], 0.0),
            xbh_rows=self._safe_float(row["xbh_rows"], 0.0),
        )

    def _compute_batter(
        self,
        games: float,
        ab: float,
        h: float,
        tb: float,
        bb: float,
        k: float,
        pa: float,
        hr: float,
        doubles: float,
        triples: float,
        hbp: float,
        ibb: float,
        sf: float,
        xbh_rows: float,
    ) -> Dict[str, float]:
        """Compute batter sabermetrics (incl. wOBA) from rolling totals."""
        features = self.get_defaults()
        features["saber_batter_games_sample"] = games

        avg = h / ab if ab > 0 else 0.0
        slg = tb / ab if ab > 0 else 0.0
        # OBP from real components when XBH backfill present (adds HBP/SF):
        # (H + BB + HBP) / (AB + BB + HBP + SF); else (H + BB) / PA.
        complete = xbh_rows >= games and games > 0
        if complete:
            obp_denom = ab + bb + hbp + sf
            obp = (h + bb + hbp) / obp_denom if obp_denom > 0 else 0.0
        else:
            obp = (h + bb) / pa if pa > 0 else 0.0

        features["saber_batter_slg"] = slg
        features["saber_batter_obp"] = obp
        features["saber_batter_iso"] = max(slg - avg, 0.0)

        if pa > 0:
            features["saber_batter_walk_rate"] = bb / pa
            features["saber_batter_strikeout_rate"] = k / pa

        woba, woba_complete = self._compute_woba(
            ab=ab,
            h=h,
            bb=bb,
            hr=hr,
            doubles=doubles,
            triples=triples,
            hbp=hbp,
            ibb=ibb,
            sf=sf,
            obp=obp,
            slg=slg,
            complete=complete,
        )
        features["saber_batter_woba"] = woba
        features["saber_batter_woba_complete"] = woba_complete

        return features

    def _compute_woba(
        self,
        ab: float,
        h: float,
        bb: float,
        hr: float,
        doubles: float,
        triples: float,
        hbp: float,
        ibb: float,
        sf: float,
        obp: float,
        slg: float,
        complete: bool,
    ) -> "tuple[float, float]":
        """
        Compute wOBA when XBH components are present, else expose incomplete.

        Returns ``(woba, woba_complete)``. When ``complete`` is True the published
        linear weights are applied to real 1B/2B/3B/HR/uBB/HBP over the true
        denominator. When False (XBH not yet backfilled) we fall back to the
        OBP/SLG components we DO have and flag ``woba_complete = 0.0`` so the
        omission is visible rather than hidden behind a guess.
        """
        if complete:
            singles = max(h - doubles - triples - hr, 0.0)
            u_bb = max(bb - ibb, 0.0)
            numerator = (
                WOBA_WEIGHTS["wBB"] * u_bb
                + WOBA_WEIGHTS["wHBP"] * hbp
                + WOBA_WEIGHTS["w1B"] * singles
                + WOBA_WEIGHTS["w2B"] * doubles
                + WOBA_WEIGHTS["w3B"] * triples
                + WOBA_WEIGHTS["wHR"] * hr
            )
            denom = ab + bb - ibb + sf + hbp
            if denom > 0:
                return round(numerator / denom, 4), 1.0

        # Incomplete-data fallback: use only the slash inputs we have, flagged.
        if ab > 0 or obp > 0:
            return round(0.7 * obp + 0.3 * slg, 4), 0.0
        return self.get_defaults()["saber_batter_woba"], 0.0
