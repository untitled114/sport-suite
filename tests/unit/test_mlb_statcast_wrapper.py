"""
Unit tests for mlb.scripts.utilities.statcast_wrapper.

pybaseball is never called - the StatcastWrapper takes an injected ``statcast_fn``.
The pitch frames are small and hand-built; they exercise the aggregation math (rates,
barrels, sweet spot, chase and whiff, putaway, batters-faced and plate-appearance counts).
"""

import sys
import types
from datetime import date, datetime
from unittest.mock import MagicMock

import pandas as pd
import pytest

from mlb.scripts.utilities.statcast_wrapper import StatcastWrapper, _to_date, month_chunks


# ---------------------------------------------------------------------------
# _to_date
# ---------------------------------------------------------------------------
def test_to_date_none():
    assert _to_date(None) is None


def test_to_date_passes_date_through():
    d = date(2024, 7, 1)
    assert _to_date(d) is d  # plain date returned as-is (not via pd.to_datetime)


def test_to_date_datetime_coerced():
    # datetime (not a plain date) goes through pd.to_datetime -> date
    assert _to_date(datetime(2024, 7, 1, 12, 0)) == date(2024, 7, 1)


def test_to_date_unparseable_returns_none():
    assert _to_date("not-a-date") is None


# ---------------------------------------------------------------------------
# _enable_cache / _statcast real-pybaseball path
# ---------------------------------------------------------------------------
def test_default_statcast_calls_pybaseball(monkeypatch):
    """No injected fn -> _enable_cache + real pybaseball.statcast are used."""
    fake_pb = types.ModuleType("pybaseball")
    fake_pb.cache = MagicMock()
    fake_pb.statcast = MagicMock(return_value=pd.DataFrame({"x": [1]}))
    monkeypatch.setitem(sys.modules, "pybaseball", fake_pb)

    w = StatcastWrapper()  # statcast_fn None -> _enable_cache() called
    fake_pb.cache.enable.assert_called_once()

    out = w._statcast("2024-07-01", "2024-07-02")
    fake_pb.statcast.assert_called_once_with(start_dt="2024-07-01", end_dt="2024-07-02")
    assert len(out) == 1


# ---------------------------------------------------------------------------
# aggregate_* return empty after the notna() filter removes all rows
# ---------------------------------------------------------------------------
def test_aggregate_pitchers_all_null_ids_returns_empty():
    df = pd.DataFrame(
        [
            {
                "pitcher": None,
                "game_pk": None,
                "description": "ball",
                "type": "B",
                "zone": 5,
                "pitch_type": "FF",
                "release_speed": 95.0,
                "strikes": 0,
                "at_bat_number": 1,
                "events": None,
            }
        ]
    )
    assert StatcastWrapper().aggregate_pitchers(df).empty


def test_aggregate_batters_all_null_ids_returns_empty():
    df = pd.DataFrame(
        [
            {
                "batter": None,
                "game_pk": None,
                "description": "ball",
                "type": "B",
                "zone": 5,
                "pitch_type": "FF",
                "release_speed": 95.0,
                "strikes": 0,
                "at_bat_number": 1,
                "events": None,
            }
        ]
    )
    assert StatcastWrapper().aggregate_batters(df).empty


# ---------------------------------------------------------------------------
# month_chunks
# ---------------------------------------------------------------------------


def test_month_chunks_in_season_only():
    chunks = month_chunks("2024-01-15", "2024-12-15")
    months = [c[0][5:7] for c in chunks]
    # Only Mar(03)..Oct(10) kept; Jan/Feb/Nov/Dec dropped.
    assert months == ["03", "04", "05", "06", "07", "08", "09", "10"]


def test_month_chunks_clamps_to_range():
    chunks = month_chunks("2024-03-20", "2024-04-10")
    assert chunks == [("2024-03-20", "2024-03-31"), ("2024-04-01", "2024-04-10")]


def test_month_chunks_spans_years():
    chunks = month_chunks("2024-09-15", "2025-04-05")
    assert chunks[0] == ("2024-09-15", "2024-09-30")
    assert chunks[1] == ("2024-10-01", "2024-10-31")
    # Nov/Dec/Jan/Feb skipped, next is 2025-03
    assert chunks[2] == ("2025-03-01", "2025-03-31")
    assert chunks[-1] == ("2025-04-01", "2025-04-05")


def test_month_chunks_invalid_range():
    assert month_chunks("2024-05-01", "2024-04-01") == []
    assert month_chunks(None, "2024-04-01") == []


# ---------------------------------------------------------------------------
# fetch_range - injected statcast_fn, chunked + concatenated
# ---------------------------------------------------------------------------


def test_fetch_range_concats_chunks():
    calls = []

    def fake(start, end):
        calls.append((start, end))
        return pd.DataFrame({"x": [1, 2]})

    w = StatcastWrapper(statcast_fn=fake)
    out = w.fetch_range("2024-03-20", "2024-04-10")
    assert len(calls) == 2  # two month chunks
    assert len(out) == 4  # 2 rows per chunk


def test_fetch_range_empty():
    w = StatcastWrapper(statcast_fn=lambda s, e: pd.DataFrame())
    assert w.fetch_range("2024-04-01", "2024-04-30").empty


# ---------------------------------------------------------------------------
# Pitcher aggregation
# ---------------------------------------------------------------------------


def _pitch(**kw):
    """Build one fake pitch row with sensible defaults."""
    base = {
        "pitcher": 100,
        "batter": 500,
        "game_pk": 9001,
        "game_date": "2024-07-01",
        "description": "ball",
        "type": "B",
        "zone": 5,
        "pitch_type": "FF",
        "release_speed": 95.0,
        "strikes": 0,
        "at_bat_number": 1,
        "events": None,
        "launch_speed": None,
        "launch_angle": None,
        "launch_speed_angle": None,
        "estimated_ba_using_speedangle": None,
        "estimated_slg_using_speedangle": None,
        "estimated_woba_using_speedangle": None,
        "woba_denom": None,
    }
    base.update(kw)
    return base


def test_aggregate_pitchers_rates():
    # 10 pitches, one pitcher, one game. Construct known counts.
    rows = [
        # 2 swinging strikes (whiff), in zone, swings
        _pitch(description="swinging_strike", type="S", zone=5),
        _pitch(description="swinging_strike", type="S", zone=5),
        # 1 foul_tip (counts as swstr + whiff + swing)
        _pitch(description="foul_tip", type="S", zone=5),
        # 2 called strikes
        _pitch(description="called_strike", type="S", zone=5),
        _pitch(description="called_strike", type="S", zone=5),
        # 1 foul (swing, not whiff, not swstr)
        _pitch(description="foul", type="S", zone=5),
        # 1 chase: swing on out-of-zone pitch (zone 13)
        _pitch(description="foul", type="S", zone=13),
        # 1 take out-of-zone (ball, zone 14) -> denominator for chase
        _pitch(description="ball", type="B", zone=14),
        # 2 plain balls in zone
        _pitch(description="ball", type="B", zone=5),
        _pitch(description="ball", type="B", zone=5),
    ]
    df = pd.DataFrame(rows)
    out = StatcastWrapper(statcast_fn=lambda *a: df).aggregate_pitchers(df)
    assert len(out) == 1
    r = out.iloc[0]
    assert r["player_id"] == 100 and r["game_id"] == 9001
    assert r["pitches"] == 10
    # swinging strikes = 2 swinging_strike + 1 foul_tip = 3
    assert r["swinging_strikes"] == 3
    assert r["called_strikes"] == 2
    assert r["swstr_pct"] == pytest.approx(3 / 10)
    assert r["csw_pct"] == pytest.approx((2 + 3) / 10)
    # swings = 3 swstr + 2 fouls = 5 ; whiffs = 3 -> 3/5
    assert r["whiff_pct"] == pytest.approx(3 / 5)
    # out-of-zone pitches = zone 13 + zone 14 = 2 ; chases = 1 (foul on 13)
    assert r["chase_pct"] == pytest.approx(1 / 2)
    # all pitches FF @ 95 -> fastball velo 95
    assert r["avg_fastball_velo"] == pytest.approx(95.0)


def test_aggregate_pitchers_strikeouts_bf_putaway():
    rows = [
        # at-bat 1 ends in a strikeout on a 2-strike pitch
        _pitch(at_bat_number=1, strikes=0, description="called_strike", type="S"),
        _pitch(at_bat_number=1, strikes=1, description="foul", type="S"),
        _pitch(
            at_bat_number=1,
            strikes=2,
            description="swinging_strike",
            type="S",
            events="strikeout",
        ),
        # at-bat 2 ends in a single (no strikeout)
        _pitch(at_bat_number=2, strikes=0, description="hit_into_play", type="X", events="single"),
    ]
    df = pd.DataFrame(rows)
    r = StatcastWrapper().aggregate_pitchers(df).iloc[0]
    assert r["strikeouts"] == 1
    assert r["batters_faced"] == 2  # two distinct at_bat_numbers
    # two-strike pitches = 1 (only the K pitch had strikes==2) -> putaway 1/1
    assert r["putaway_pct"] == pytest.approx(1.0)


def test_aggregate_pitchers_no_fastball_velo_none():
    df = pd.DataFrame([_pitch(pitch_type="SL", description="ball")])
    r = StatcastWrapper().aggregate_pitchers(df).iloc[0]
    assert r["avg_fastball_velo"] is None


def test_aggregate_pitchers_empty():
    assert StatcastWrapper().aggregate_pitchers(pd.DataFrame()).empty


# ---------------------------------------------------------------------------
# Batter aggregation
# ---------------------------------------------------------------------------


def test_aggregate_batters_batted_ball_metrics():
    rows = [
        # batted ball 1: hard hit (100), barrel (lsa 6), sweet spot (la 20)
        _pitch(
            type="X",
            description="hit_into_play",
            launch_speed=100.0,
            launch_angle=20.0,
            launch_speed_angle=6,
            estimated_ba_using_speedangle=0.8,
            estimated_slg_using_speedangle=2.0,
            estimated_woba_using_speedangle=1.2,
            woba_denom=1,
            at_bat_number=1,
        ),
        # batted ball 2: soft (80), not barrel, outside sweet spot (la 40)
        _pitch(
            type="X",
            description="hit_into_play",
            launch_speed=80.0,
            launch_angle=40.0,
            launch_speed_angle=2,
            estimated_ba_using_speedangle=0.1,
            estimated_slg_using_speedangle=0.1,
            estimated_woba_using_speedangle=0.2,
            woba_denom=1,
            at_bat_number=2,
        ),
    ]
    df = pd.DataFrame(rows)
    r = StatcastWrapper().aggregate_batters(df).iloc[0]
    assert r["player_id"] == 500 and r["game_id"] == 9001
    assert r["batted_balls"] == 2
    assert r["avg_exit_velo"] == pytest.approx(90.0)  # (100+80)/2
    assert r["hard_hit_pct"] == pytest.approx(0.5)  # only the 100 mph one
    assert r["barrel_pct"] == pytest.approx(0.5)  # one lsa==6
    assert r["sweet_spot_pct"] == pytest.approx(0.5)  # la 20 in [8,32], 40 out
    assert r["avg_launch_angle"] == pytest.approx(30.0)
    assert r["xba"] == pytest.approx((0.8 + 0.1) / 2)
    assert r["xslg"] == pytest.approx((2.0 + 0.1) / 2)
    assert r["xwoba"] == pytest.approx((1.2 + 0.2) / 2)
    assert r["pa"] == 2


def test_aggregate_batters_whiff_chase():
    rows = [
        # swing + miss in zone
        _pitch(description="swinging_strike", type="S", zone=5, at_bat_number=1),
        # foul (swing, not whiff) in zone
        _pitch(description="foul", type="S", zone=5, at_bat_number=1),
        # chase: swing on out-of-zone (zone 12)
        _pitch(description="swinging_strike", type="S", zone=12, at_bat_number=1),
        # take out-of-zone (zone 11) -> chase denominator
        _pitch(description="ball", type="B", zone=11, at_bat_number=1),
    ]
    df = pd.DataFrame(rows)
    r = StatcastWrapper().aggregate_batters(df).iloc[0]
    # swings = 3 (2 swstr + 1 foul) ; whiffs = 2 -> 2/3
    assert r["whiff_pct"] == pytest.approx(2 / 3)
    # out-of-zone = zone 12 + zone 11 = 2 ; chases = 1 -> 1/2
    assert r["chase_pct"] == pytest.approx(0.5)
    assert r["batted_balls"] == 0
    assert r["avg_exit_velo"] is None


def test_aggregate_batters_empty():
    assert StatcastWrapper().aggregate_batters(pd.DataFrame()).empty
