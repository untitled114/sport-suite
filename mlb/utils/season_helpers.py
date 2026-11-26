"""
Season helper utilities for MLB date/season conversions.

MLB seasons use the START year convention (unlike the NBA END-year convention):
- 2024 season = 2024
- 2025 season = 2025

The regular season runs ~late March / April through September. The postseason
is October. A few games (Opening Series) occasionally occur in late March; we
treat any month from March onward as belonging to that calendar year's season.
The off-season is November-February.
"""

from datetime import date, datetime, timedelta
from typing import Optional, Tuple, Union
from zoneinfo import ZoneInfo

# Approximate calendar boundaries (EST). Exact Opening Day shifts year to year;
# late-March openers are handled by treating March as season start.
SEASON_START_MONTH = 3  # March (late-March Opening Series / April Opening Day)
REGULAR_SEASON_END_MONTH = 9  # September (regular season ends late Sept/early Oct)
POSTSEASON_MONTH = 10  # October (Wild Card through World Series)

# Exact season boundaries (Opening Day -> last regular-season day), per season.
# Single source of truth: the temporal feature extractor and every scheduled job's season
# gate read this table.
# Add each new season before Opening Day; unknown seasons fall back to months.
MLB_SEASON_BOUNDS = {
    2024: {"start": "2024-03-28", "regular_end": "2024-09-29", "postseason_start": "2024-10-01"},
    2025: {"start": "2025-03-27", "regular_end": "2025-09-28", "postseason_start": "2025-09-30"},
    2026: {"start": "2026-03-26", "regular_end": "2026-09-27", "postseason_start": "2026-09-29"},
    # 2027 from the MLB statsapi seasons and schedule endpoints:
    # first full slate 03-25, last regular-season day 09-26. The Wild Card
    # schedule was not yet published, so postseason_start is the seasons
    # endpoint's postSeasonStartDate; no games fall between it and the first
    # Wild Card game, so the gates and features behave the same.
    2027: {"start": "2027-03-25", "regular_end": "2027-09-26", "postseason_start": "2027-09-27"},
}

# Days after the last regular-season game during which validation keeps running, so the final
# day is complete: late stat corrections arrive for a few days after a game.
VALIDATION_SETTLE_DAYS = 5

_EST = ZoneInfo("America/New_York")


def date_to_season(game_date: Union[str, date, datetime]) -> int:
    """
    Convert a game date to an MLB season identifier (START year convention).

    Args:
        game_date: Date as string (YYYY-MM-DD), date, or datetime object

    Returns:
        Season year (e.g., 2025 for the 2025 season)

    Examples:
        date_to_season('2025-04-15') -> 2025  (April 2025 = 2025 season)
        date_to_season('2025-09-28') -> 2025  (Sept 2025 = 2025 season)
        date_to_season('2025-10-20') -> 2025  (Oct 2025 = 2025 postseason)
        date_to_season('2025-01-10') -> 2024  (Jan 2025 = prior 2024 season)
    """
    game_date = _coerce_date(game_date)
    year = game_date.year
    month = game_date.month

    # March onward belongs to the current calendar year's season.
    # Jan/Feb (off-season) belong to the PREVIOUS season.
    if month >= SEASON_START_MONTH:
        return year
    return year - 1


def season_to_date_range(season: int) -> tuple:
    """
    Get the approximate date range for an MLB season (incl. postseason).

    Args:
        season: Season year (START year convention)

    Returns:
        Tuple of (start_date, end_date)
    """
    return (
        date(season, SEASON_START_MONTH, 20),  # ~Opening Series (late March)
        date(season, 11, 5),  # ~end of World Series (early November)
    )


def is_postseason(game_date: Union[str, date, datetime]) -> bool:
    """
    Return True if the date falls in the MLB postseason (October).

    The postseason runs from the Wild Card round (early October) through the
    World Series (which can spill into early November in some years).

    Examples:
        is_postseason('2025-07-04') -> False
        is_postseason('2025-10-15') -> True
    """
    game_date = _coerce_date(game_date)
    return game_date.month in (POSTSEASON_MONTH, 11)


def regular_season_bounds(season: int) -> Optional[Tuple[date, date]]:
    """(opening_day, last_regular_season_day) for a season, or None if unknown."""
    bounds = MLB_SEASON_BOUNDS.get(season)
    if not bounds:
        return None
    return _coerce_date(bounds["start"]), _coerce_date(bounds["regular_end"])


def is_regular_season(game_date: Union[str, date, datetime]) -> bool:
    """
    Return True if the date falls in the MLB regular season.

    Uses the exact Opening Day / last-day bounds when the season is in
    MLB_SEASON_BOUNDS; otherwise falls back to the month window (March-September).

    Examples:
        is_regular_season('2025-04-15') -> True
        is_regular_season('2026-09-27') -> True   (last regular-season day)
        is_regular_season('2026-09-28') -> False  (exact bound, not month)
        is_regular_season('2025-10-15') -> False
        is_regular_season('2025-01-10') -> False
    """
    game_date = _coerce_date(game_date)
    bounds = regular_season_bounds(game_date.year)
    if bounds:
        return bounds[0] <= game_date <= bounds[1]
    return SEASON_START_MONTH <= game_date.month <= REGULAR_SEASON_END_MONTH


def is_season_closing_day(day: Union[str, date, datetime]) -> bool:
    """True on the day after the last regular-season game, when season-closing jobs run."""
    day = _coerce_date(day)
    bounds = regular_season_bounds(day.year)
    return bool(bounds) and day == bounds[1] + timedelta(days=1)


def validation_open(day: Union[str, date, datetime]) -> bool:
    """True while validation should run: the regular season plus VALIDATION_SETTLE_DAYS after it."""
    day = _coerce_date(day)
    if is_regular_season(day):
        return True
    bounds = regular_season_bounds(day.year)
    return bool(bounds) and bounds[1] < day <= bounds[1] + timedelta(days=VALIDATION_SETTLE_DAYS)


def today_et() -> date:
    """Today's date in America/New_York."""
    return datetime.now(_EST).date()


def _coerce_date(game_date: Union[str, date, datetime]) -> date:
    """Coerce a string/datetime/date to a ``date`` object."""
    if isinstance(game_date, str):
        return datetime.strptime(game_date[:10], "%Y-%m-%d").date()
    if isinstance(game_date, datetime):
        return game_date.date()
    if isinstance(game_date, date):
        return game_date
    raise TypeError(f"Unsupported date type: {type(game_date)!r}")
