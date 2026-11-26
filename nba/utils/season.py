"""NBA seasons from the calendar (America/New_York): the one season helper.

A season is named by its END year everywhere in the database ("2025-26" -> 2026). It runs
October 1 to September 30, so a date in October or later belongs to the next year's season.
Season dates (the opener) come from ``games.games``, never from a hard-coded calendar.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Tuple, Union
from zoneinfo import ZoneInfo

EST = ZoneInfo("America/New_York")

DateLike = Union[date, datetime, str]


def _as_date(d: DateLike) -> date:
    """A date from a date, a datetime (its own calendar date) or a "YYYY-MM-DD..." string."""
    if isinstance(d, datetime):
        return d.date()
    if isinstance(d, date):
        return d
    if isinstance(d, str):
        return datetime.strptime(d[:10], "%Y-%m-%d").date()
    raise TypeError(f"not a date: {d!r}")


def season_for_date(d: DateLike) -> int:
    """The season a date belongs to, by end year: October or later -> year + 1, else the year."""
    d = _as_date(d)
    return d.year + 1 if d.month >= 10 else d.year


def season_end_year(season: Union[str, int]) -> int:
    """A season's end year: "2026-27" -> 2027; an int is already an end year."""
    if isinstance(season, int):
        return season
    return int(season[:4]) + 1


def season_label(end_year: int) -> str:
    """The "YYYY-YY" name nba_api uses: 2026 -> "2025-26"."""
    start = end_year - 1
    return f"{start}-{end_year % 100:02d}"


def current_season(today: date | None = None) -> int:
    """Today's season (ET) by end year."""
    return season_for_date(today or datetime.now(EST).date())


def season_bounds(end_year: int) -> Tuple[date, date]:
    """The calendar span of a season: October 1 of the start year to September 30."""
    return date(end_year - 1, 10, 1), date(end_year, 9, 30)


def season_start(end_year: int, conn: Any) -> date:
    """The date of the season's first regular-season game in ``games.games``.

    Never guessed: LookupError when the table has no game typed 'Regular Season' for that season,
    naming how many rows it does hold (rows with a NULL season_type are not counted as regular
    season; on 2026-11-10 the 2024-25 games were filed under season 2024, untyped).
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT MIN(game_date) FILTER (WHERE season_type = 'Regular Season'), COUNT(*), "
            "COUNT(*) FILTER (WHERE season_type IS NULL) FROM games.games WHERE season = %s",
            (end_year,),
        )
        first, rows, untyped = cur.fetchone()
    if first is None:
        raise LookupError(
            f"games.games has no Regular Season game for season {end_year} "
            f"({rows} rows, {untyped} with no season_type)"
        )
    return first


def current_nba_season(today: date | None = None) -> str:
    """The season string nba_api expects, e.g. "2026-27". A new season starts in October."""
    return season_label(current_season(today))


def previous_nba_season(today: date | None = None) -> str:
    """The season before ``current_nba_season``, e.g. "2025-26"."""
    return season_label(current_season(today) - 1)
