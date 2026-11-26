#!/usr/bin/env python3
"""
Statcast Loader
===============
Pulls raw Statcast pitch data (Baseball Savant via pybaseball), aggregates it
into per-(pitcher, game) and per-(batter, game) metric rows, and upserts those
rows into ``mlb_players.statcast_pitcher_games`` and
``mlb_players.statcast_batter_games``.

Because Statcast ``pitcher``/``batter`` == our ``player_id`` and
``game_pk`` == our ``game_id``, no name matching is needed - aggregates join
directly to our existing tables.

Usage:
    # Specific seasons (in-season months only, Mar-Oct)
    python -m mlb.scripts.loaders.load_statcast --season 2024 2025 2026

    # Explicit date range
    python -m mlb.scripts.loaders.load_statcast --start 2024-03-20 --end 2024-10-31

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
from mlb.scripts.utilities.statcast_wrapper import StatcastWrapper

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

DB_CONFIG = get_players_db_config()

PITCHER_COLS = (
    "player_id",
    "game_id",
    "game_date",
    "pitches",
    "swinging_strikes",
    "called_strikes",
    "swstr_pct",
    "csw_pct",
    "whiff_pct",
    "chase_pct",
    "avg_fastball_velo",
    "putaway_pct",
    "strikeouts",
    "batters_faced",
)

BATTER_COLS = (
    "player_id",
    "game_id",
    "game_date",
    "batted_balls",
    "avg_exit_velo",
    "hard_hit_pct",
    "barrel_pct",
    "sweet_spot_pct",
    "avg_launch_angle",
    "xba",
    "xwoba",
    "xslg",
    "whiff_pct",
    "chase_pct",
    "pa",
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
    out = []
    for _, r in df.iterrows():
        out.append(tuple(_none(r.get(c)) for c in cols))
    return out


def _upsert_pitchers(conn, rows: List[tuple]) -> int:
    """Upsert pitcher aggregate rows into statcast_pitcher_games."""
    if not rows:
        return 0
    cur = conn.cursor()
    try:
        execute_values(
            cur,
            """
            INSERT INTO statcast_pitcher_games
            (player_id, game_id, game_date, pitches, swinging_strikes, called_strikes,
             swstr_pct, csw_pct, whiff_pct, chase_pct, avg_fastball_velo, putaway_pct,
             strikeouts, batters_faced)
            VALUES %s
            ON CONFLICT (player_id, game_id) DO UPDATE SET
                game_date = EXCLUDED.game_date,
                pitches = EXCLUDED.pitches,
                swinging_strikes = EXCLUDED.swinging_strikes,
                called_strikes = EXCLUDED.called_strikes,
                swstr_pct = EXCLUDED.swstr_pct,
                csw_pct = EXCLUDED.csw_pct,
                whiff_pct = EXCLUDED.whiff_pct,
                chase_pct = EXCLUDED.chase_pct,
                avg_fastball_velo = EXCLUDED.avg_fastball_velo,
                putaway_pct = EXCLUDED.putaway_pct,
                strikeouts = EXCLUDED.strikeouts,
                batters_faced = EXCLUDED.batters_faced
            """,
            rows,
        )
        conn.commit()
        return len(rows)
    finally:
        cur.close()


def _upsert_batters(conn, rows: List[tuple]) -> int:
    """Upsert batter aggregate rows into statcast_batter_games."""
    if not rows:
        return 0
    cur = conn.cursor()
    try:
        execute_values(
            cur,
            """
            INSERT INTO statcast_batter_games
            (player_id, game_id, game_date, batted_balls, avg_exit_velo, hard_hit_pct,
             barrel_pct, sweet_spot_pct, avg_launch_angle, xba, xwoba, xslg,
             whiff_pct, chase_pct, pa)
            VALUES %s
            ON CONFLICT (player_id, game_id) DO UPDATE SET
                game_date = EXCLUDED.game_date,
                batted_balls = EXCLUDED.batted_balls,
                avg_exit_velo = EXCLUDED.avg_exit_velo,
                hard_hit_pct = EXCLUDED.hard_hit_pct,
                barrel_pct = EXCLUDED.barrel_pct,
                sweet_spot_pct = EXCLUDED.sweet_spot_pct,
                avg_launch_angle = EXCLUDED.avg_launch_angle,
                xba = EXCLUDED.xba,
                xwoba = EXCLUDED.xwoba,
                xslg = EXCLUDED.xslg,
                whiff_pct = EXCLUDED.whiff_pct,
                chase_pct = EXCLUDED.chase_pct,
                pa = EXCLUDED.pa
            """,
            rows,
        )
        conn.commit()
        return len(rows)
    finally:
        cur.close()


def _season_range(season: int) -> Tuple[str, str]:
    """Full-year range for a season; the wrapper clamps to in-season months."""
    return (date(season, 1, 1).isoformat(), date(season, 12, 31).isoformat())


def load_statcast(
    start: str,
    end: str,
    wrapper: Optional[StatcastWrapper] = None,
    conn=None,
) -> Tuple[int, int]:
    """
    Pull + aggregate + upsert Statcast for an inclusive [start, end] range.

    Pulls are chunked monthly (in-season months only) so each chunk's raw pitch
    count and resulting per-game aggregate counts can be logged independently.

    Args:
        start: "YYYY-MM-DD" inclusive.
        end: "YYYY-MM-DD" inclusive.
        wrapper: Optional StatcastWrapper (tests inject a mock).
        conn: Optional open DB connection (tests inject a mock).

    Returns:
        (pitcher_rows_upserted, batter_rows_upserted) across the whole range.
    """
    from mlb.scripts.utilities.statcast_wrapper import month_chunks

    wrapper = wrapper or StatcastWrapper()
    owns_conn = conn is None
    if owns_conn:
        conn = psycopg2.connect(**DB_CONFIG)

    total_p = 0
    total_b = 0
    try:
        for cs, ce in month_chunks(start, end):
            raw = wrapper.fetch_range(cs, ce)
            n_pitches = 0 if raw is None else len(raw)

            p_df = wrapper.aggregate_pitchers(raw)
            b_df = wrapper.aggregate_batters(raw)
            p_rows = _rows_from_df(p_df, PITCHER_COLS)
            b_rows = _rows_from_df(b_df, BATTER_COLS)

            n_p = _upsert_pitchers(conn, p_rows)
            n_b = _upsert_batters(conn, b_rows)
            total_p += n_p
            total_b += n_b
            logger.info(
                "Chunk %s..%s: %d pitches -> %d pitcher-games, %d batter-games",
                cs,
                ce,
                n_pitches,
                n_p,
                n_b,
            )

        logger.info(
            "Statcast load %s..%s complete: %d pitcher-games, %d batter-games",
            start,
            end,
            total_p,
            total_b,
        )
        return total_p, total_b
    finally:
        if owns_conn:
            conn.close()


def main():
    parser = argparse.ArgumentParser(description="Load Statcast aggregates into mlb_players")
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

    grand_p = 0
    grand_b = 0
    for start, end in ranges:
        p, b = load_statcast(start, end)
        grand_p += p
        grand_b += b

    logger.info("TOTAL upserted: %d pitcher-games, %d batter-games", grand_p, grand_b)


if __name__ == "__main__":
    main()
