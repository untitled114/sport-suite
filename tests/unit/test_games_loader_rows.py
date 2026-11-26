"""The games loader builds one games row (both scores) and two team-log rows (full box
stats) per game from NBA's two LeagueGameFinder rows.

Regressions covered: the away score must be written, and the box-score columns must reach
the team logs (without them the team season stats find no teams).
Fixture: real capture, see tests/fixtures/nba_api/README.md.
"""

import json
from pathlib import Path

import pandas as pd

from nba.scripts.loaders.load_nba_games_incremental import build_game_rows

FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "fixtures/nba_api/leaguegamefinder_2026-27_regular_20261029.json"
)


def _df():
    rs = json.loads(FIXTURE.read_text())["resultSets"][0]
    return pd.DataFrame(rs["rowSet"], columns=rs["headers"])


def test_one_games_row_and_two_team_rows_per_game():
    games, logs = build_game_rows(_df(), season_year=2027, after_date="2026-10-01")
    assert len(games) == 68
    assert len(logs) == 136
    assert all(g["home_score"] is not None and g["away_score"] is not None for g in games)


def test_scores_and_box_stats_come_from_the_right_rows():
    games, logs = build_game_rows(_df(), season_year=2027, after_date="2026-10-01")
    g = next(g for g in games if g["game_id"] == "0022600140")
    assert (g["home_team"], g["away_team"]) == ("NOP", "DEN")
    assert (g["home_score"], g["away_score"]) == (91, 104)
    den = next(r for r in logs if r["game_id"] == "0022600140" and r["team_abbrev"] == "DEN")
    assert den["opponent"] == "NOP" and den["is_home"] is False
    assert (den["points"], den["fg_attempted"], den["ft_attempted"], den["turnovers"]) == (
        104,
        80,
        19,
        17,
    )
    assert (den["rebounds"], den["assists"]) == (42, 30)


def test_box_stats_present_for_every_team_row():
    _, logs = build_game_rows(_df(), season_year=2027, after_date="2026-10-01")
    cols = [
        "fg_made",
        "fg_attempted",
        "three_pt_made",
        "three_pt_attempted",
        "ft_made",
        "ft_attempted",
        "rebounds",
        "assists",
        "turnovers",
    ]
    assert all(r[c] is not None for r in logs for c in cols)


def test_after_date_filters_already_loaded_games():
    games, logs = build_game_rows(_df(), season_year=2027, after_date="2026-10-27")
    assert {str(g["game_date"].date()) for g in games} == {"2026-10-28"}
    assert len(logs) == 2 * len(games)
