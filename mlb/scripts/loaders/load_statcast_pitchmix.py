#!/usr/bin/env python3
"""
Statcast Pitch-Mix / Batted-Ball / BvP Loader
=============================================
Pulls RAW Baseball Savant pitch-by-pitch data (pybaseball via StatcastWrapper),
aggregates it into the raw-pitch feature tables, and upserts them:

    statcast_pitcher_pitchmix_games   (pitch-mix, STRIKEOUTS)
    statcast_batter_battedball_games  (batted-ball, HITS / TOTAL_BASES)
    bvp_encounters                    (per-encounter real BvP, H2H)
    matchup_history                   (cumulative BvP rollup, rebuilt from above)

Because Statcast ``pitcher``/``batter`` == our ``player_id`` and
``game_pk`` == our ``game_id``, no name matching is needed.

Usage:
    python -m mlb.scripts.loaders.load_statcast_pitchmix --season 2024 2025 2026
    python -m mlb.scripts.loaders.load_statcast_pitchmix --start 2024-03-20 --end 2026-06-18

Dates are Eastern (America/New_York).
"""

import argparse
import logging
import sys
from datetime import date
from pathlib import Path
from typing import List, Optional, Tuple

import pandas as pd
import psycopg2
from psycopg2.extras import execute_values

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from mlb.config.database import get_players_db_config
from mlb.scripts.utilities.pitchmix_aggregator import (
    aggregate_battedball,
    aggregate_bvp_encounters,
    aggregate_pitchmix,
)
from mlb.scripts.utilities.statcast_wrapper import StatcastWrapper, month_chunks
from mlb.sql import named_query

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

DB_CONFIG = get_players_db_config()

PITCHMIX_COLS = (
    "player_id",
    "game_id",
    "game_date",
    "num_pitch_types",
    "fastball_velo",
    "fastball_spin",
    "breaking_spin",
    "zone_rate",
    "first_strike_rate",
    "fastball_whiff_rate",
    "slider_whiff_rate",
    "curve_whiff_rate",
    "changeup_whiff_rate",
)

BATTEDBALL_COLS = (
    "player_id",
    "game_id",
    "game_date",
    "bbe",
    "gb_rate",
    "fb_rate",
    "ld_rate",
    "popup_rate",
    "pull_rate",
)

BVP_COLS = (
    "batter_id",
    "pitcher_id",
    "game_id",
    "game_date",
    "pa",
    "hits",
    "k",
    "bb",
    "tb",
)


def _none(v):
    """Convert NaN/NA to None for psycopg2; pass real values through."""
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (ValueError, TypeError):
        pass
    return v


def _rows_from_df(df: pd.DataFrame, cols: Tuple[str, ...]) -> List[tuple]:
    """Convert an aggregate DataFrame into ordered insert tuples (NaN -> None)."""
    if df is None or len(df) == 0:
        return []
    return [tuple(_none(r.get(c)) for c in cols) for _, r in df.iterrows()]


def _upsert_pitchmix(conn, rows: List[tuple]) -> int:
    """Upsert pitch-mix rows into statcast_pitcher_pitchmix_games."""
    if not rows:
        return 0
    cur = conn.cursor()
    try:
        execute_values(
            cur,
            """
            INSERT INTO statcast_pitcher_pitchmix_games
            (player_id, game_id, game_date, num_pitch_types, fastball_velo, fastball_spin,
             breaking_spin, zone_rate, first_strike_rate, fastball_whiff_rate,
             slider_whiff_rate, curve_whiff_rate, changeup_whiff_rate)
            VALUES %s
            ON CONFLICT (player_id, game_id) DO UPDATE SET
                game_date = EXCLUDED.game_date,
                num_pitch_types = EXCLUDED.num_pitch_types,
                fastball_velo = EXCLUDED.fastball_velo,
                fastball_spin = EXCLUDED.fastball_spin,
                breaking_spin = EXCLUDED.breaking_spin,
                zone_rate = EXCLUDED.zone_rate,
                first_strike_rate = EXCLUDED.first_strike_rate,
                fastball_whiff_rate = EXCLUDED.fastball_whiff_rate,
                slider_whiff_rate = EXCLUDED.slider_whiff_rate,
                curve_whiff_rate = EXCLUDED.curve_whiff_rate,
                changeup_whiff_rate = EXCLUDED.changeup_whiff_rate
            """,
            rows,
        )
        conn.commit()
        return len(rows)
    finally:
        cur.close()


def _upsert_battedball(conn, rows: List[tuple]) -> int:
    """Upsert batted-ball rows into statcast_batter_battedball_games."""
    if not rows:
        return 0
    cur = conn.cursor()
    try:
        execute_values(
            cur,
            """
            INSERT INTO statcast_batter_battedball_games
            (player_id, game_id, game_date, bbe, gb_rate, fb_rate, ld_rate,
             popup_rate, pull_rate)
            VALUES %s
            ON CONFLICT (player_id, game_id) DO UPDATE SET
                game_date = EXCLUDED.game_date,
                bbe = EXCLUDED.bbe,
                gb_rate = EXCLUDED.gb_rate,
                fb_rate = EXCLUDED.fb_rate,
                ld_rate = EXCLUDED.ld_rate,
                popup_rate = EXCLUDED.popup_rate,
                pull_rate = EXCLUDED.pull_rate
            """,
            rows,
        )
        conn.commit()
        return len(rows)
    finally:
        cur.close()


def _upsert_bvp(conn, rows: List[tuple]) -> int:
    """Upsert per-encounter BvP rows into bvp_encounters."""
    if not rows:
        return 0
    cur = conn.cursor()
    try:
        execute_values(
            cur,
            """
            INSERT INTO bvp_encounters
            (batter_id, pitcher_id, game_id, game_date, pa, hits, k, bb, tb)
            VALUES %s
            ON CONFLICT (batter_id, pitcher_id, game_id) DO UPDATE SET
                game_date = EXCLUDED.game_date,
                pa = EXCLUDED.pa,
                hits = EXCLUDED.hits,
                k = EXCLUDED.k,
                bb = EXCLUDED.bb,
                tb = EXCLUDED.tb
            """,
            rows,
        )
        conn.commit()
        return len(rows)
    finally:
        cur.close()


def rebuild_matchup_history(conn) -> int:
    """
    Rebuild the cumulative matchup_history rollup from bvp_encounters.

    Recomputes the full cumulative (batter, pitcher) table from the
    per-encounter rows in a single SQL pass (TRUNCATE + INSERT ... SELECT). This
    keeps the rollup consistent with the leakage-safe per-encounter source.

    Returns the number of (batter, pitcher) rollup rows written.
    """
    cur = conn.cursor()
    try:
        cur.execute("TRUNCATE TABLE matchup_history")
        cur.execute(named_query("matchup_history_rebuild"))
        n = cur.rowcount
        conn.commit()
        return n
    finally:
        cur.close()


def _season_range(season: int) -> Tuple[str, str]:
    """Full-year range for a season; the wrapper clamps to in-season months."""
    return (date(season, 1, 1).isoformat(), date(season, 12, 31).isoformat())


def load_statcast_pitchmix(
    start: str,
    end: str,
    wrapper: Optional[StatcastWrapper] = None,
    conn=None,
) -> Tuple[int, int, int]:
    """
    Pull + aggregate + upsert pitch-mix / batted-ball / BvP for [start, end].

    Pulls are chunked monthly (in-season months only). After all chunks are
    upserted, the matchup_history rollup is rebuilt from bvp_encounters.

    Args:
        start: "YYYY-MM-DD" inclusive.
        end: "YYYY-MM-DD" inclusive.
        wrapper: Optional StatcastWrapper (tests inject a mock).
        conn: Optional open DB connection (tests inject a mock).

    Returns:
        (pitchmix_rows, battedball_rows, bvp_rows) upserted across the range.
    """
    wrapper = wrapper or StatcastWrapper()
    owns_conn = conn is None
    if owns_conn:
        conn = psycopg2.connect(**DB_CONFIG)

    total_pm = 0
    total_bb = 0
    total_bvp = 0
    try:
        for cs, ce in month_chunks(start, end):
            raw = wrapper.fetch_range(cs, ce)
            n_pitches = 0 if raw is None else len(raw)

            pm_rows = _rows_from_df(aggregate_pitchmix(raw), PITCHMIX_COLS)
            bb_rows = _rows_from_df(aggregate_battedball(raw), BATTEDBALL_COLS)
            bvp_rows = _rows_from_df(aggregate_bvp_encounters(raw), BVP_COLS)

            n_pm = _upsert_pitchmix(conn, pm_rows)
            n_bb = _upsert_battedball(conn, bb_rows)
            n_bvp = _upsert_bvp(conn, bvp_rows)
            total_pm += n_pm
            total_bb += n_bb
            total_bvp += n_bvp
            logger.info(
                "Chunk %s..%s: %d pitches -> %d pitchmix, %d battedball, %d bvp",
                cs,
                ce,
                n_pitches,
                n_pm,
                n_bb,
                n_bvp,
            )

        n_rollup = rebuild_matchup_history(conn)
        logger.info(
            "Pitch-mix load %s..%s complete: %d pitchmix, %d battedball, "
            "%d bvp-encounters, %d matchup_history rollup rows",
            start,
            end,
            total_pm,
            total_bb,
            total_bvp,
            n_rollup,
        )
        return total_pm, total_bb, total_bvp
    finally:
        if owns_conn:
            conn.close()


def main():
    parser = argparse.ArgumentParser(
        description="Load Statcast pitch-mix / batted-ball / BvP into mlb_players"
    )
    parser.add_argument("--season", type=int, nargs="+", default=None, help="Season years")
    parser.add_argument("--start", type=str, default=None, help="Inclusive start YYYY-MM-DD")
    parser.add_argument("--end", type=str, default=None, help="Inclusive end YYYY-MM-DD")
    args = parser.parse_args()

    if args.start and args.end:
        ranges = [(args.start, args.end)]
    elif args.season:
        ranges = [_season_range(s) for s in args.season]
    else:
        from mlb.scripts.utilities.statsapi_wrapper import get_current_season

        ranges = [_season_range(get_current_season())]

    grand_pm = 0
    grand_bb = 0
    grand_bvp = 0
    for start, end in ranges:
        pm, bb, bvp = load_statcast_pitchmix(start, end)
        grand_pm += pm
        grand_bb += bb
        grand_bvp += bvp

    logger.info(
        "TOTAL upserted: %d pitchmix, %d battedball, %d bvp-encounters",
        grand_pm,
        grand_bb,
        grand_bvp,
    )


if __name__ == "__main__":
    main()
