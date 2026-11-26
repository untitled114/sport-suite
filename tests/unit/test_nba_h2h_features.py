"""The NBA head-to-head extractor computes its features from the rows its query returns.

Regression covered: the query must return every column the calculations use, under the names
they use. It is a named file whose aliases are those names.
"""

import re
from unittest.mock import MagicMock

import pandas as pd

from nba.features.extractors.h2h_features import H2HFeatureExtractor
from nba.sql import named_query

USED = ("game_date", "points", "rebounds", "assists", "three_pointers_made", "minutes",
        "field_goals_made", "field_goals_attempted", "three_pointers_attempted", "is_home")  # fmt: skip


def _rows():
    return pd.DataFrame(
        {
            "game_date": pd.to_datetime(["2026-01-20", "2025-12-02", "2025-03-11", "2024-11-05"]),
            "points": [30, 22, 26, 18],
            "rebounds": [9, 7, 8, 10],
            "assists": [5, 4, 6, 3],
            "three_pointers_made": [4, 2, 3, 1],
            "minutes": [37, 35, 36, 33],
            "field_goals_made": [11, 8, 9, 7],
            "field_goals_attempted": [20, 18, 19, 16],
            "three_pointers_attempted": [9, 7, 8, 5],
            "is_home": [True, False, True, False],
        }
    )


def test_the_query_returns_every_column_the_calculations_use():
    select = named_query("player_games_vs_opponent").split("FROM player_game_logs")[0]
    produced = {(m.group(2) or m.group(1)) for m in re.finditer(r"gl\.(\w+)(?: AS (\w+))?", select)}
    assert set(USED) <= produced, set(USED) - produced


def test_features_come_from_the_rows():
    extractor = H2HFeatureExtractor(conn=MagicMock())
    extractor._safe_query = MagicMock(return_value=_rows())
    out = extractor.extract("Any Player", "2026-02-01", "POINTS", opponent_team="MIA")
    query, params = extractor._safe_query.call_args[0][:2]
    assert query == named_query("player_games_vs_opponent")
    assert params == ("Any Player", "MIA", "2026-02-01")
    assert out["h2h_career_games"] == 4
    assert out["h2h_career_points_avg"] == 24.0
    assert out["h2h_career_minutes_avg"] == 35.25
    assert set(out) == set(H2HFeatureExtractor.FEATURE_NAMES)


def test_no_opponent_or_no_history_gives_the_defaults():
    extractor = H2HFeatureExtractor(conn=MagicMock())
    assert extractor.extract("Any Player", "2026-02-01", "POINTS") == extractor.get_defaults()
    extractor._safe_query = MagicMock(return_value=None)
    out = extractor.extract("Any Player", "2026-02-01", "POINTS", opponent_team="MIA")
    assert out == extractor.get_defaults()
