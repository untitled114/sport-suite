"""The nightly box-score loader stores only games between two NBA teams
(nba/scripts/fetch_daily_stats.is_nba_game)."""

from nba.scripts.fetch_daily_stats import is_nba_game
from nba.utils.team_utils import normalize_team_abbrev


def test_only_games_between_two_nba_teams_are_stored():
    """2026-02-15's All-Star events came on ESPN's scoreboard with the teams STARS, STRIPES and
    WORLD, which are not league games; the season's real games are kept,
    under ESPN's own abbreviations once normalized."""
    for home, away in (("STARS", "STRIPES"), ("WORLD", "STARS"), ("STRIPES", "WORLD")):
        assert not is_nba_game(normalize_team_abbrev(home), normalize_team_abbrev(away))
    assert not is_nba_game("BOS", normalize_team_abbrev("WORLD"))
    assert not is_nba_game(None, "BOS")
    for home, away in (
        ("BOS", "WSH"),
        ("DET", "ORL"),
        ("DAL", "DEN"),
        ("GS", "NY"),
        ("UTAH", "SA"),
    ):
        assert is_nba_game(normalize_team_abbrev(home), normalize_team_abbrev(away))
