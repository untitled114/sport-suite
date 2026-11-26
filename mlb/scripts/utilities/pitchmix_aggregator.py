"""
Pitch-Mix / Batted-Ball / BvP Raw-Pitch Aggregator
===================================================
Aggregates RAW Baseball Savant pitch-by-pitch rows (pybaseball) into the
per-game pitch-mix, per-game batted-ball, and per-encounter batter-vs-pitcher
rows that feed the new raw-pitch feature suite.

These are the features the aggregate-only ``statcast_*_games`` tables cannot
express:

  * pitch-mix     -> ``statcast_pitcher_pitchmix_games``  (STRIKEOUTS)
  * batted-ball   -> ``statcast_batter_battedball_games``  (HITS / TOTAL_BASES)
  * real BvP      -> ``bvp_encounters``                    (H2H)

CRITICAL ID FACTS (no name matching):
    * Statcast ``pitcher`` / ``batter`` == our ``player_id``.
    * Statcast ``game_pk`` == our ``game_id``.

Every row is aggregated from Statcast pitches.
Raw pitches are pulled (cached) via ``StatcastWrapper.fetch_range`` upstream.
"""

import logging
from datetime import date, datetime
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pitch-type groupings (Statcast pitch_type codes).
# ---------------------------------------------------------------------------
FASTBALL_TYPES = frozenset({"FF", "SI", "FC"})
# Breaking balls: slider/sweeper/slurve + curve family.
BREAKING_TYPES = frozenset({"SL", "ST", "SV", "CS", "CU", "KC"})
SLIDER_TYPES = frozenset({"SL", "ST", "SV"})  # slider + sweeper + slurve
CURVE_TYPES = frozenset({"CU", "KC", "CS"})  # curveball + knuckle-curve + slow-curve
CHANGEUP_TYPES = frozenset({"CH", "FS", "FO"})  # changeup + splitter + forkball

# Swing / whiff description semantics (mirror statcast_wrapper).
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
WHIFF_DESCRIPTIONS = frozenset(
    {"swinging_strike", "swinging_strike_blocked", "foul_tip", "missed_bunt", "bunt_foul_tip"}
)
STRIKE_TYPE = "S"  # Statcast `type` column: B=ball, S=strike, X=in-play

# In-zone strike-zone numbers (1-9). 11-14 are out of zone.
IN_ZONE = frozenset({1, 2, 3, 4, 5, 6, 7, 8, 9})

# Batted-ball type buckets.
BB_GROUND = "ground_ball"
BB_FLY = "fly_ball"
BB_LINE = "line_drive"
BB_POPUP = "popup"

# BvP hit/outcome event semantics.
HIT_EVENTS = frozenset({"single", "double", "triple", "home_run"})
K_EVENTS = frozenset({"strikeout", "strikeout_double_play"})
BB_EVENTS = frozenset({"walk", "intent_walk"})
TOTAL_BASES = {"single": 1, "double": 2, "triple": 3, "home_run": 4}


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


def _safe_div(num, den):
    """Return num/den as float, or None when denominator is zero/NaN."""
    if den is None or den == 0 or pd.isna(den):
        return None
    return float(num) / float(den)


def _iter_groups(df: pd.DataFrame, keys):
    """
    Iterate ``(key_tuple, sub_DataFrame)`` groups WITHOUT pandas ``groupby``.

    pandas' ``groupby`` triggers an internal numpy reduction that can interact
    badly with coverage instrumentation; manual index grouping is reduction-free
    and order-preserving (first-seen key order).
    """
    buckets: "dict" = {}
    cols = [df[k].tolist() for k in keys]
    for i in range(len(df)):
        key = tuple(cols[c][i] for c in range(len(keys)))
        buckets.setdefault(key, []).append(i)
    for key, idx in buckets.items():
        yield key, df.iloc[idx]


def _count_true(series) -> int:
    """Count truthy values in a boolean-ish Series (numpy-reduction-free)."""
    return int(sum(1 for v in series.tolist() if bool(v)))


def _mean_where(g: pd.DataFrame, mask_col: str, value_col: str):
    """Mean of ``value_col`` over rows where ``mask_col`` is True; None if empty."""
    masks = g[mask_col].tolist()
    vals = g[value_col].tolist()
    kept = [float(v) for m, v in zip(masks, vals) if bool(m) and v is not None and not pd.isna(v)]
    return sum(kept) / len(kept) if kept else None


def _whiff_rate(g: pd.DataFrame, mask_col: str):
    """Whiffs / swings within a pitch-group mask; None when no swings."""
    masks = g[mask_col].tolist()
    swings = g["_swing"].tolist()
    whiffs = g["_whiff"].tolist()
    n_swing = sum(1 for m, s in zip(masks, swings) if bool(m) and bool(s))
    n_whiff = sum(1 for m, w in zip(masks, whiffs) if bool(m) and bool(w))
    return _safe_div(n_whiff, n_swing)


def _spray_is_pull(hc_x, hc_y, stand: str) -> Optional[bool]:
    """
    Classify a batted ball as pulled using Statcast hit coordinates.

    Spray angle (degrees, home plate ~ (125.42, 198.27)):
        angle = atan( (hc_x - 125.42) / (198.27 - hc_y) )
    Positive angle = ball to the right side of the field (3B/LF for a RHB).
    A pull for a RHB (stand == 'R') is the left side -> negative angle.
    A pull for a LHB (stand == 'L') is the right side -> positive angle.

    Returns True/False, or None when coordinates/handedness are missing.
    """
    import math

    if hc_x is None or hc_y is None or pd.isna(hc_x) or pd.isna(hc_y):
        return None
    if stand not in ("R", "L"):
        return None
    angle = math.degrees(math.atan((float(hc_x) - 125.42) / (198.27 - float(hc_y) + 1e-9)))
    if stand == "R":
        return angle < 0.0  # RHB pulls to the left side
    return angle > 0.0  # LHB pulls to the right side


def aggregate_pitchmix(df: pd.DataFrame) -> pd.DataFrame:
    """
    Aggregate raw pitches into per-(pitcher, game_pk) pitch-mix rows.

    Columns produced (one row per player_id/game_id):
        num_pitch_types, fastball_velo, fastball_spin, breaking_spin,
        zone_rate, first_strike_rate, fastball_whiff_rate, slider_whiff_rate,
        curve_whiff_rate, changeup_whiff_rate, game_date.
    """
    if df is None or len(df) == 0:
        return pd.DataFrame()

    d = df[df["pitcher"].notna() & df["game_pk"].notna()].copy()
    if len(d) == 0:
        return pd.DataFrame()

    d["pitcher"] = d["pitcher"].astype("int64")
    d["game_pk"] = d["game_pk"].astype("int64")
    desc = d["description"].astype("string")
    d["_swing"] = desc.isin(SWING_DESCRIPTIONS).fillna(False)
    d["_whiff"] = desc.isin(WHIFF_DESCRIPTIONS).fillna(False)
    pt = d["pitch_type"].astype("string")
    d["_pt"] = pt
    d["_fastball"] = pt.isin(FASTBALL_TYPES).fillna(False)
    d["_slider"] = pt.isin(SLIDER_TYPES).fillna(False)
    d["_curve"] = pt.isin(CURVE_TYPES).fillna(False)
    d["_changeup"] = pt.isin(CHANGEUP_TYPES).fillna(False)
    d["_breaking"] = pt.isin(BREAKING_TYPES).fillna(False)
    zone = pd.to_numeric(d.get("zone"), errors="coerce")
    d["_in_zone"] = zone.isin(list(IN_ZONE)).fillna(False)
    d["_speed"] = pd.to_numeric(d.get("release_speed"), errors="coerce")
    d["_spin"] = pd.to_numeric(d.get("release_spin_rate"), errors="coerce")
    d["_balls"] = pd.to_numeric(d.get("balls"), errors="coerce")
    d["_strikes"] = pd.to_numeric(d.get("strikes"), errors="coerce")
    d["_is_strike"] = (d["type"].astype("string") == STRIKE_TYPE).fillna(False)
    # First pitch of a PA: balls == 0 and strikes == 0.
    d["_first_pitch"] = (d["_balls"] == 0) & (d["_strikes"] == 0)

    rows = []
    for (pid, gpk), g in _iter_groups(d, ("pitcher", "game_pk")):
        pitches = len(g)
        n_types = len({pt for pt in g["_pt"].tolist() if pt is not None and not pd.isna(pt)})
        zone_rate = _safe_div(_count_true(g["_in_zone"]), pitches)
        first = g[g["_first_pitch"]]
        fs_rate = _safe_div(_count_true(first["_is_strike"]), len(first))

        rows.append(
            {
                "player_id": int(pid),
                "game_id": int(gpk),
                "game_date": _to_date(g["game_date"].iloc[0]),
                "num_pitch_types": n_types,
                "fastball_velo": _mean_where(g, "_fastball", "_speed"),
                "fastball_spin": _mean_where(g, "_fastball", "_spin"),
                "breaking_spin": _mean_where(g, "_breaking", "_spin"),
                "zone_rate": zone_rate,
                "first_strike_rate": fs_rate,
                "fastball_whiff_rate": _whiff_rate(g, "_fastball"),
                "slider_whiff_rate": _whiff_rate(g, "_slider"),
                "curve_whiff_rate": _whiff_rate(g, "_curve"),
                "changeup_whiff_rate": _whiff_rate(g, "_changeup"),
            }
        )

    return pd.DataFrame(rows)


def aggregate_battedball(df: pd.DataFrame) -> pd.DataFrame:
    """
    Aggregate raw pitches into per-(batter, game_pk) batted-ball rows.

    Batted-ball events (BBE) are rows with a non-null ``bb_type``. Rates are
    shares of BBE; pull_rate uses Statcast hit-coordinate spray geometry,
    handedness-adjusted (``stand``).
    """
    if df is None or len(df) == 0:
        return pd.DataFrame()

    d = df[df["batter"].notna() & df["game_pk"].notna()].copy()
    if len(d) == 0:
        return pd.DataFrame()

    d["batter"] = d["batter"].astype("int64")
    d["game_pk"] = d["game_pk"].astype("int64")
    d["_bb_type"] = d["bb_type"].astype("string")
    bbe_all = d[d["_bb_type"].notna()].copy()
    if len(bbe_all) == 0:
        return pd.DataFrame()

    rows = []
    for (bid, gpk), g in _iter_groups(bbe_all, ("batter", "game_pk")):
        n_bbe = len(g)
        bb_types = g["_bb_type"].tolist()
        gb = sum(1 for t in bb_types if t == BB_GROUND)
        fb = sum(1 for t in bb_types if t == BB_FLY)
        ld = sum(1 for t in bb_types if t == BB_LINE)
        pu = sum(1 for t in bb_types if t == BB_POPUP)

        pulls = 0
        spray_known = 0
        stand_val = g["stand"].iloc[0] if "stand" in g.columns else None
        for _, r in g.iterrows():
            res = _spray_is_pull(r.get("hc_x"), r.get("hc_y"), r.get("stand", stand_val))
            if res is not None:
                spray_known += 1
                if res:
                    pulls += 1

        rows.append(
            {
                "player_id": int(bid),
                "game_id": int(gpk),
                "game_date": _to_date(g["game_date"].iloc[0]),
                "bbe": n_bbe,
                "gb_rate": _safe_div(gb, n_bbe),
                "fb_rate": _safe_div(fb, n_bbe),
                "ld_rate": _safe_div(ld, n_bbe),
                "popup_rate": _safe_div(pu, n_bbe),
                "pull_rate": _safe_div(pulls, spray_known),
            }
        )

    return pd.DataFrame(rows)


def aggregate_bvp_encounters(df: pd.DataFrame) -> pd.DataFrame:
    """
    Aggregate raw pitches into per-(batter, pitcher, game_pk) BvP encounter rows.

    Uses only TERMINAL pitches (rows with a non-null ``events`` - the last pitch
    of a plate appearance) so each PA is counted once. Real per-PA outcomes:
        pa   = number of terminal events (true plate appearances)
        hits = single + double + triple + home_run
        k    = strikeout (+ strikeout_double_play)
        bb   = walk (+ intent_walk)
        tb   = total bases (1B+2*2B+3*3B+4*HR)
    """
    if df is None or len(df) == 0:
        return pd.DataFrame()

    d = df[df["batter"].notna() & df["pitcher"].notna() & df["game_pk"].notna()].copy()
    if len(d) == 0:
        return pd.DataFrame()

    d["_events"] = d["events"].astype("string")
    term = d[d["_events"].notna()].copy()
    if len(term) == 0:
        return pd.DataFrame()

    term["batter"] = term["batter"].astype("int64")
    term["pitcher"] = term["pitcher"].astype("int64")
    term["game_pk"] = term["game_pk"].astype("int64")
    term["_hit"] = term["_events"].isin(HIT_EVENTS)
    term["_k"] = term["_events"].isin(K_EVENTS)
    term["_bb"] = term["_events"].isin(BB_EVENTS)
    term["_tb"] = term["_events"].map(TOTAL_BASES).fillna(0).astype(int)

    rows = []
    for (bid, pid, gpk), g in _iter_groups(term, ("batter", "pitcher", "game_pk")):
        rows.append(
            {
                "batter_id": int(bid),
                "pitcher_id": int(pid),
                "game_id": int(gpk),
                "game_date": _to_date(g["game_date"].iloc[0]),
                "pa": int(len(g)),
                "hits": _count_true(g["_hit"]),
                "k": _count_true(g["_k"]),
                "bb": _count_true(g["_bb"]),
                "tb": int(sum(int(v) for v in g["_tb"].tolist())),
            }
        )

    return pd.DataFrame(rows)
