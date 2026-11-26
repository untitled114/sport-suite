"""
Statcast Wrapper (Baseball Savant via pybaseball)
=================================================
Thin wrapper around ``pybaseball.statcast`` that pulls raw pitch-by-pitch
Statcast data and aggregates it into the per-(pitcher, game) and
per-(batter, game) metric rows that feed the STRIKEOUTS / HITS / TOTAL_BASES
stat types.

CRITICAL ID FACTS (no name matching required):
    * Statcast ``pitcher`` and ``batter`` columns are MLBAM player IDs, which
      EQUAL our statsapi ``player_id`` (``mlb_players.*``).
    * Statcast ``game_pk`` EQUALS our ``game_id``.

Caching is enabled (``pybaseball.cache.enable()``) so repeated/backfill pulls
do not re-hit Baseball Savant. Bulk pulls are chunked monthly and restricted to
the MLB in-season months (March-October) to avoid empty off-season queries.

All dates are handled in EST (America/New_York) at the loader boundary.
"""

import calendar
import logging
from datetime import date, datetime
from typing import Callable, List, Optional, Tuple

import pandas as pd

logger = logging.getLogger(__name__)

# MLB in-season months (regular season + postseason). Spring training (Feb) and
# the winter off-season are skipped - Statcast returns no regular pitches there.
IN_SEASON_MONTHS = (3, 4, 5, 6, 7, 8, 9, 10)

# ---------------------------------------------------------------------------
# Statcast description / type semantics (verified against a real sample)
# ---------------------------------------------------------------------------
# A "swing" is any pitch where the batter offered at the ball.
SWING_DESCRIPTIONS = frozenset(
    {
        "hit_into_play",
        "foul",
        "foul_tip",
        "swinging_strike",
        "swinging_strike_blocked",
        "foul_pitchout",
        "swinging_pitchout",
        "foul_bunt",
        "missed_bunt",
        "bunt_foul_tip",
    }
)
# A "whiff" is a swing-and-miss.
WHIFF_DESCRIPTIONS = frozenset(
    {"swinging_strike", "swinging_strike_blocked", "foul_tip", "missed_bunt", "bunt_foul_tip"}
)
# Swinging-strike (for SwStr%) - strictly the bat-missed-ball outcomes.
SWINGING_STRIKE_DESCRIPTIONS = frozenset({"swinging_strike", "swinging_strike_blocked", "foul_tip"})
CALLED_STRIKE_DESCRIPTION = "called_strike"

# Fastball pitch types for average velocity.
FASTBALL_PITCH_TYPES = frozenset({"FF", "SI", "FC"})

# Statcast strike-zone numbering: 1-9 are IN the zone, 11-14 are OUT of the zone.
IN_ZONE = frozenset({1, 2, 3, 4, 5, 6, 7, 8, 9})


def _enable_cache() -> None:
    """Enable pybaseball's on-disk cache (idempotent)."""
    import pybaseball

    try:
        pybaseball.cache.enable()
    except Exception as e:  # pragma: no cover - cache is best-effort
        logger.debug("pybaseball cache enable failed (continuing uncached): %s", e)


def _to_date(value) -> Optional[date]:
    """Coerce a date-ish value (str / datetime / Timestamp) to a date."""
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    try:
        return pd.to_datetime(value).date()
    except (ValueError, TypeError):
        return None


def month_chunks(start: str, end: str) -> List[Tuple[str, str]]:
    """
    Split an inclusive [start, end] date range into per-month (start, end)
    chunks, keeping ONLY in-season months (Mar-Oct). Each chunk is clamped to
    the requested range.

    Args:
        start: "YYYY-MM-DD" inclusive start.
        end: "YYYY-MM-DD" inclusive end.

    Returns:
        List of ("YYYY-MM-DD", "YYYY-MM-DD") month chunks (chronological).
    """
    s = _to_date(start)
    e = _to_date(end)
    if s is None or e is None or s > e:
        return []

    chunks: List[Tuple[str, str]] = []
    year, month = s.year, s.month
    while (year, month) <= (e.year, e.month):
        if month in IN_SEASON_MONTHS:
            first = date(year, month, 1)
            last = date(year, month, calendar.monthrange(year, month)[1])
            chunk_start = max(first, s)
            chunk_end = min(last, e)
            if (
                chunk_start <= chunk_end
            ):  # pragma: no cover - always true (months iterated overlap [s,e])
                chunks.append((chunk_start.isoformat(), chunk_end.isoformat()))
        month += 1
        if month > 12:
            month = 1
            year += 1
    return chunks


class StatcastWrapper:
    """Pulls and aggregates raw Statcast pitch data."""

    def __init__(self, statcast_fn: Optional[Callable] = None):
        """
        Args:
            statcast_fn: Optional injection of the ``statcast(start_dt, end_dt)``
                callable (used by tests). Defaults to ``pybaseball.statcast``.
        """
        self._statcast_fn = statcast_fn
        if statcast_fn is None:
            _enable_cache()

    def _statcast(self, start: str, end: str) -> pd.DataFrame:
        """Resolve and call the underlying statcast function."""
        if self._statcast_fn is not None:
            return self._statcast_fn(start, end)
        import pybaseball

        return pybaseball.statcast(start_dt=start, end_dt=end)

    def fetch_range(self, start: str, end: str) -> pd.DataFrame:
        """
        Pull raw Statcast pitches for an inclusive [start, end] range, chunked
        monthly over in-season months only.

        Args:
            start: "YYYY-MM-DD" inclusive.
            end: "YYYY-MM-DD" inclusive.

        Returns:
            Concatenated raw pitch DataFrame (empty if nothing returned).
        """
        frames: List[pd.DataFrame] = []
        for cs, ce in month_chunks(start, end):
            logger.info("Statcast pull %s -> %s ...", cs, ce)
            df = self._statcast(cs, ce)
            n = 0 if df is None else len(df)
            logger.info("  chunk %s -> %s: %d pitches", cs, ce, n)
            if df is not None and len(df) > 0:
                frames.append(df)
        if not frames:
            return pd.DataFrame()
        return pd.concat(frames, ignore_index=True)

    # ------------------------------------------------------------------
    # Aggregation
    # ------------------------------------------------------------------

    @staticmethod
    def _prep(df: pd.DataFrame) -> pd.DataFrame:
        """Add derived per-pitch boolean/helper columns used by aggregation."""
        out = df.copy()
        desc = out["description"].astype("string")

        out["_is_swing"] = desc.isin(SWING_DESCRIPTIONS).fillna(False)
        out["_is_whiff"] = desc.isin(WHIFF_DESCRIPTIONS).fillna(False)
        out["_is_swstr"] = desc.isin(SWINGING_STRIKE_DESCRIPTIONS).fillna(False)
        out["_is_called_strike"] = (desc == CALLED_STRIKE_DESCRIPTION).fillna(False)

        zone = pd.to_numeric(out.get("zone"), errors="coerce")
        # In-zone if zone in 1-9; out-of-zone if it's a real zone number not in 1-9.
        out["_in_zone"] = zone.isin(list(IN_ZONE)).fillna(False)
        out["_out_zone"] = zone.notna() & ~out["_in_zone"]

        pt = out["pitch_type"].astype("string")
        out["_is_fastball"] = pt.isin(FASTBALL_PITCH_TYPES).fillna(False)

        out["_is_bip"] = (out["type"].astype("string") == "X").fillna(False)
        out["_is_strikeout"] = (out["events"].astype("string") == "strikeout").fillna(False)

        return out

    @staticmethod
    def _safe_div(num: float, den: float):
        """Return num/den or None when denominator is zero/NaN."""
        if den is None or den == 0 or pd.isna(den):
            return None
        return float(num) / float(den)

    def aggregate_pitchers(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Aggregate raw pitches into per-(pitcher, game_pk) pitcher metric rows.

        Definitions:
            pitches            = pitch count thrown
            swinging_strikes   = swinging_strike + swinging_strike_blocked + foul_tip
            called_strikes     = called_strike
            swstr_pct          = swinging_strikes / pitches
            csw_pct            = (called + swinging) / pitches
            whiff_pct          = whiffs / swings
            chase_pct          = swings on out-of-zone pitches / out-of-zone pitches
            avg_fastball_velo  = mean release_speed where pitch_type in {FF,SI,FC}
            strikeouts         = count of events == "strikeout"
            batters_faced      = distinct (at_bat_number) for this pitcher in the game
            putaway_pct        = strikeouts / two_strike_pitches
                                 (share of 2-strike pitches that ended the K)

        Returns:
            DataFrame with one row per (player_id, game_id) and metric columns.
        """
        if df is None or len(df) == 0:
            return pd.DataFrame()

        d = self._prep(df)
        d = d[d["pitcher"].notna() & d["game_pk"].notna()].copy()
        if len(d) == 0:
            return pd.DataFrame()

        d["pitcher"] = d["pitcher"].astype("int64")
        d["game_pk"] = d["game_pk"].astype("int64")
        d["_two_strike"] = pd.to_numeric(d["strikes"], errors="coerce").fillna(0).astype(int) == 2
        d["_ab_key"] = (
            d["game_pk"].astype(str)
            + "_"
            + pd.to_numeric(d.get("at_bat_number"), errors="coerce")
            .fillna(-1)
            .astype(int)
            .astype(str)
        )

        rows = []
        for (pid, gpk), g in d.groupby(["pitcher", "game_pk"], sort=False):
            pitches = len(g)
            swstr = int(g["_is_swstr"].sum())
            called = int(g["_is_called_strike"].sum())
            swings = int(g["_is_swing"].sum())
            whiffs = int(g["_is_whiff"].sum())
            out_zone = int(g["_out_zone"].sum())
            chases = int((g["_is_swing"] & g["_out_zone"]).sum())
            strikeouts = int(g["_is_strikeout"].sum())
            two_strike = int(g["_two_strike"].sum())
            bf = int(g["_ab_key"].nunique())
            fb = g.loc[g["_is_fastball"], "release_speed"]
            fb_velo = float(fb.mean()) if len(fb.dropna()) > 0 else None

            rows.append(
                {
                    "player_id": int(pid),
                    "game_id": int(gpk),
                    "game_date": _to_date(g["game_date"].iloc[0]),
                    "pitches": pitches,
                    "swinging_strikes": swstr,
                    "called_strikes": called,
                    "swstr_pct": self._safe_div(swstr, pitches),
                    "csw_pct": self._safe_div(called + swstr, pitches),
                    "whiff_pct": self._safe_div(whiffs, swings),
                    "chase_pct": self._safe_div(chases, out_zone),
                    "avg_fastball_velo": fb_velo,
                    "putaway_pct": self._safe_div(strikeouts, two_strike),
                    "strikeouts": strikeouts,
                    "batters_faced": bf,
                }
            )

        return pd.DataFrame(rows)

    def aggregate_batters(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Aggregate raw pitches into per-(batter, game_pk) batter metric rows.

        Definitions (batted balls = pitches with type == "X"):
            batted_balls      = count of balls in play
            avg_exit_velo     = mean launch_speed over batted balls
            hard_hit_pct      = share of batted balls with launch_speed >= 95
            barrel_pct        = share of batted balls with launch_speed_angle == 6
                                (Statcast's official barrel classification)
            sweet_spot_pct    = share of batted balls with launch_angle in [8, 32]
            avg_launch_angle  = mean launch_angle over batted balls
            xba               = mean estimated_ba_using_speedangle over batted balls
            xwoba             = mean estimated_woba_using_speedangle over PA outcomes
                                (rows where woba_denom is present)
            xslg              = mean estimated_slg_using_speedangle over batted balls
                                (Savant's own xSLG)
            whiff_pct         = whiffs / swings (this batter's swings)
            chase_pct         = swings on out-of-zone / out-of-zone pitches seen
            pa                = distinct plate appearances (at_bat_number) in the game

        Returns:
            DataFrame with one row per (player_id, game_id) and metric columns.
        """
        if df is None or len(df) == 0:
            return pd.DataFrame()

        d = self._prep(df)
        d = d[d["batter"].notna() & d["game_pk"].notna()].copy()
        if len(d) == 0:
            return pd.DataFrame()

        d["batter"] = d["batter"].astype("int64")
        d["game_pk"] = d["game_pk"].astype("int64")
        d["_launch_speed"] = pd.to_numeric(d.get("launch_speed"), errors="coerce")
        d["_launch_angle"] = pd.to_numeric(d.get("launch_angle"), errors="coerce")
        d["_lsa"] = pd.to_numeric(d.get("launch_speed_angle"), errors="coerce")
        d["_xba"] = pd.to_numeric(d.get("estimated_ba_using_speedangle"), errors="coerce")
        d["_xslg"] = pd.to_numeric(d.get("estimated_slg_using_speedangle"), errors="coerce")
        d["_xwoba"] = pd.to_numeric(d.get("estimated_woba_using_speedangle"), errors="coerce")
        d["_woba_denom"] = pd.to_numeric(d.get("woba_denom"), errors="coerce")
        d["_ab_num"] = pd.to_numeric(d.get("at_bat_number"), errors="coerce")

        rows = []
        for (bid, gpk), g in d.groupby(["batter", "game_pk"], sort=False):
            bip = g[g["_is_bip"]]
            n_bip = len(bip)
            ls = bip["_launch_speed"].dropna()
            la = bip["_launch_angle"].dropna()

            avg_ev = float(ls.mean()) if len(ls) > 0 else None
            hard_hit = self._safe_div(int((bip["_launch_speed"] >= 95).sum()), n_bip)
            barrels = self._safe_div(int((bip["_lsa"] == 6).sum()), n_bip)
            sweet = self._safe_div(
                int(((bip["_launch_angle"] >= 8) & (bip["_launch_angle"] <= 32)).sum()),
                n_bip,
            )
            avg_la = float(la.mean()) if len(la) > 0 else None
            xba = float(bip["_xba"].dropna().mean()) if bip["_xba"].notna().any() else None
            xslg = float(bip["_xslg"].dropna().mean()) if bip["_xslg"].notna().any() else None

            woba_rows = g[g["_woba_denom"].fillna(0) > 0]
            xwoba = (
                float(woba_rows["_xwoba"].dropna().mean())
                if woba_rows["_xwoba"].notna().any()
                else None
            )

            swings = int(g["_is_swing"].sum())
            whiffs = int(g["_is_whiff"].sum())
            out_zone = int(g["_out_zone"].sum())
            chases = int((g["_is_swing"] & g["_out_zone"]).sum())
            pa = int(g["_ab_num"].dropna().nunique())

            rows.append(
                {
                    "player_id": int(bid),
                    "game_id": int(gpk),
                    "game_date": _to_date(g["game_date"].iloc[0]),
                    "batted_balls": n_bip,
                    "avg_exit_velo": avg_ev,
                    "hard_hit_pct": hard_hit,
                    "barrel_pct": barrels,
                    "sweet_spot_pct": sweet,
                    "avg_launch_angle": avg_la,
                    "xba": xba,
                    "xwoba": xwoba,
                    "xslg": xslg,
                    "whiff_pct": self._safe_div(whiffs, swings),
                    "chase_pct": self._safe_div(chases, out_zone),
                    "pa": pa,
                }
            )

        return pd.DataFrame(rows)
