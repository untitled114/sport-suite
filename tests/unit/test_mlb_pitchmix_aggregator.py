"""
Unit tests for mlb.scripts.utilities.pitchmix_aggregator.

The pitch frames are small and hand-built; they exercise the aggregation math (pitch mix,
batted ball, batter versus pitcher). No network or database access.
"""

import pandas as pd
import pytest

import mlb.scripts.utilities.pitchmix_aggregator as agg


# ---------------------------------------------------------------------------
# helpers: _to_date / _safe_div / _spray_is_pull
# ---------------------------------------------------------------------------
def test_to_date_variants():
    import datetime as dt

    assert agg._to_date(None) is None
    assert agg._to_date(dt.date(2024, 7, 1)) == dt.date(2024, 7, 1)
    assert agg._to_date("2024-07-01") == dt.date(2024, 7, 1)
    assert agg._to_date(dt.datetime(2024, 7, 1, 10, 0)) == dt.date(2024, 7, 1)
    assert agg._to_date("not-a-date") is None


def test_safe_div():
    assert agg._safe_div(1, 2) == 0.5
    assert agg._safe_div(5, 0) is None
    assert agg._safe_div(5, None) is None
    assert agg._safe_div(5, float("nan")) is None


def test_spray_is_pull_rhb_and_lhb():
    # RHB pulls to the left side -> hc_x well left of home plate (125.42).
    assert agg._spray_is_pull(60.0, 100.0, "R") is True
    assert agg._spray_is_pull(190.0, 100.0, "R") is False
    # LHB pulls to the right side.
    assert agg._spray_is_pull(190.0, 100.0, "L") is True
    assert agg._spray_is_pull(60.0, 100.0, "L") is False


def test_spray_is_pull_missing_returns_none():
    assert agg._spray_is_pull(None, 100.0, "R") is None
    assert agg._spray_is_pull(60.0, None, "R") is None
    assert agg._spray_is_pull(float("nan"), 100.0, "R") is None
    assert agg._spray_is_pull(60.0, 100.0, "S") is None  # switch/unknown stand


# ---------------------------------------------------------------------------
# aggregate_pitchmix
# ---------------------------------------------------------------------------
def _pitch(**over):
    base = {
        "pitcher": 100,
        "batter": 500,
        "game_pk": 9001,
        "game_date": "2024-07-01",
        "pitch_type": "FF",
        "description": "ball",
        "type": "B",
        "release_speed": 95.0,
        "release_spin_rate": 2300.0,
        "zone": 5,
        "balls": 0,
        "strikes": 0,
        "bb_type": None,
        "events": None,
        "stand": "R",
        "hc_x": None,
        "hc_y": None,
    }
    base.update(over)
    return base


def test_aggregate_pitchmix_empty():
    assert agg.aggregate_pitchmix(None).empty
    assert agg.aggregate_pitchmix(pd.DataFrame()).empty


def test_aggregate_pitchmix_no_valid_rows():
    # pitcher/game_pk null -> filtered out -> empty.
    df = pd.DataFrame([_pitch(pitcher=None)])
    assert agg.aggregate_pitchmix(df).empty


def test_aggregate_pitchmix_metrics():
    rows = [
        # FF in zone, first pitch strike (a swinging strike = whiff + strike).
        _pitch(
            pitch_type="FF",
            description="swinging_strike",
            type="S",
            zone=5,
            balls=0,
            strikes=0,
            release_speed=96.0,
            release_spin_rate=2400.0,
        ),
        # FF foul (swing, not whiff), out of zone.
        _pitch(
            pitch_type="FF",
            description="foul",
            type="S",
            zone=12,
            balls=1,
            strikes=0,
            release_speed=94.0,
            release_spin_rate=2200.0,
        ),
        # SL swing+miss (whiff), in zone.
        _pitch(
            pitch_type="SL",
            description="swinging_strike",
            type="S",
            zone=4,
            balls=0,
            strikes=1,
            release_spin_rate=2600.0,
        ),
        # CU called strike (not a swing), out of zone.
        _pitch(
            pitch_type="CU",
            description="called_strike",
            type="S",
            zone=13,
            balls=1,
            strikes=1,
            release_spin_rate=2700.0,
        ),
    ]
    out = agg.aggregate_pitchmix(pd.DataFrame(rows))
    assert len(out) == 1
    r = out.iloc[0].to_dict()
    assert r["player_id"] == 100 and r["game_id"] == 9001
    assert r["num_pitch_types"] == 3  # FF, SL, CU
    # fastball velo = mean(96, 94) = 95.0
    assert r["fastball_velo"] == pytest.approx(95.0)
    assert r["fastball_spin"] == pytest.approx(2300.0)  # mean(2400, 2200)
    # breaking spin = mean(SL 2600, CU 2700) = 2650
    assert r["breaking_spin"] == pytest.approx(2650.0)
    # zone_rate: zones 5 and 4 are in-zone (2 of 4) = 0.5
    assert r["zone_rate"] == pytest.approx(0.5)
    # first_strike_rate: 1 first-pitch (balls0 strikes0), it was a strike -> 1.0
    assert r["first_strike_rate"] == pytest.approx(1.0)
    # fastball whiff: swings {ss, foul} = 2, whiffs {ss} = 1 -> 0.5
    assert r["fastball_whiff_rate"] == pytest.approx(0.5)
    # slider whiff: 1 swing, 1 whiff -> 1.0
    assert r["slider_whiff_rate"] == pytest.approx(1.0)
    # curve whiff: called_strike is not a swing -> 0 swings -> None
    assert r["curve_whiff_rate"] is None
    # changeup: none thrown -> None
    assert r["changeup_whiff_rate"] is None


def test_aggregate_pitchmix_velo_spin_none_when_missing():
    df = pd.DataFrame(
        [_pitch(pitch_type="FF", release_speed=float("nan"), release_spin_rate=float("nan"))]
    )
    r = agg.aggregate_pitchmix(df).iloc[0].to_dict()
    assert r["fastball_velo"] is None
    assert r["fastball_spin"] is None
    assert r["breaking_spin"] is None  # no breaking balls


# ---------------------------------------------------------------------------
# aggregate_battedball
# ---------------------------------------------------------------------------
def test_aggregate_battedball_empty():
    assert agg.aggregate_battedball(None).empty
    assert agg.aggregate_battedball(pd.DataFrame()).empty


def test_aggregate_battedball_no_valid_rows():
    df = pd.DataFrame([_pitch(batter=None, bb_type="ground_ball")])
    assert agg.aggregate_battedball(df).empty


def test_aggregate_battedball_no_bbe():
    # No bb_type anywhere -> no batted-ball events -> empty.
    df = pd.DataFrame([_pitch(bb_type=None)])
    assert agg.aggregate_battedball(df).empty


def test_aggregate_battedball_rates_and_pull():
    rows = [
        _pitch(bb_type="ground_ball", stand="R", hc_x=60.0, hc_y=100.0),  # pull
        _pitch(bb_type="fly_ball", stand="R", hc_x=190.0, hc_y=100.0),  # oppo
        _pitch(bb_type="line_drive", stand="R", hc_x=60.0, hc_y=100.0),  # pull
        _pitch(bb_type="popup", stand="R", hc_x=None, hc_y=None),  # spray unknown
    ]
    out = agg.aggregate_battedball(pd.DataFrame(rows))
    assert len(out) == 1
    r = out.iloc[0].to_dict()
    assert r["player_id"] == 500 and r["bbe"] == 4
    assert r["gb_rate"] == pytest.approx(0.25)
    assert r["fb_rate"] == pytest.approx(0.25)
    assert r["ld_rate"] == pytest.approx(0.25)
    assert r["popup_rate"] == pytest.approx(0.25)
    # spray known for 3 balls (popup had no coords), pulls = 2 -> 2/3
    assert r["pull_rate"] == pytest.approx(2.0 / 3.0)


def test_aggregate_battedball_pull_none_when_no_coords():
    df = pd.DataFrame([_pitch(bb_type="ground_ball", hc_x=None, hc_y=None)])
    r = agg.aggregate_battedball(df).iloc[0].to_dict()
    assert r["pull_rate"] is None


def test_aggregate_battedball_missing_stand_column():
    # Drop the 'stand' column entirely -> stand_val None -> pull unknown.
    df = pd.DataFrame([_pitch(bb_type="ground_ball", hc_x=60.0, hc_y=100.0)])
    df = df.drop(columns=["stand"])
    r = agg.aggregate_battedball(df).iloc[0].to_dict()
    assert r["pull_rate"] is None
    assert r["bbe"] == 1


# ---------------------------------------------------------------------------
# aggregate_bvp_encounters
# ---------------------------------------------------------------------------
def test_aggregate_bvp_empty():
    assert agg.aggregate_bvp_encounters(None).empty
    assert agg.aggregate_bvp_encounters(pd.DataFrame()).empty


def test_aggregate_bvp_no_valid_rows():
    df = pd.DataFrame([_pitch(batter=None, events="single")])
    assert agg.aggregate_bvp_encounters(df).empty


def test_aggregate_bvp_no_terminal_events():
    df = pd.DataFrame([_pitch(events=None)])
    assert agg.aggregate_bvp_encounters(df).empty


def test_aggregate_bvp_counts():
    rows = [
        _pitch(events="single"),  # hit, tb 1
        _pitch(events="home_run"),  # hit, tb 4
        _pitch(events="strikeout"),  # k
        _pitch(events="walk"),  # bb
        _pitch(events="field_out"),  # PA, no hit/k/bb
        _pitch(events=None),  # non-terminal -> ignored
    ]
    out = agg.aggregate_bvp_encounters(pd.DataFrame(rows))
    assert len(out) == 1
    r = out.iloc[0].to_dict()
    assert r["batter_id"] == 500 and r["pitcher_id"] == 100 and r["game_id"] == 9001
    assert r["pa"] == 5  # 5 terminal events
    assert r["hits"] == 2
    assert r["k"] == 1
    assert r["bb"] == 1
    assert r["tb"] == 5  # 1 + 4
