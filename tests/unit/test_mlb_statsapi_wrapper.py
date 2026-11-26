"""
Unit tests for mlb.scripts.utilities.statsapi_wrapper.

All HTTP is mocked: no network calls. The payloads are the smallest shapes that exercise the
parsing.
"""

from unittest.mock import MagicMock, patch

import pytest
import requests

from mlb.scripts.utilities.statsapi_wrapper import (
    DEFAULT_HEADERS,
    StatsAPIWrapper,
    get_current_season,
)


def _resp(payload, status=200):
    """Build a fake requests.Response-like object."""
    m = MagicMock()
    m.status_code = status
    m.json.return_value = payload
    m.raise_for_status.return_value = None
    return m


@pytest.fixture
def api():
    # rpm=0 disables sleeps for fast tests
    return StatsAPIWrapper(requests_per_minute=0, base_url="https://example.test/api/v1")


def test_no_brotli_in_headers():
    """Accept-Encoding must never include brotli (br) - known decompression bug."""
    assert "br" not in DEFAULT_HEADERS["Accept-Encoding"]
    assert DEFAULT_HEADERS["Accept-Encoding"] == "gzip, deflate"


def test_get_teams(api):
    payload = {"teams": [{"id": 147, "abbreviation": "NYY"}, {"id": 111, "abbreviation": "BOS"}]}
    with patch.object(api.session, "get", return_value=_resp(payload)) as g:
        teams = api.get_teams()
    assert len(teams) == 2
    # sportId param passed
    _, kwargs = g.call_args
    assert kwargs["params"]["sportId"] == 1


def test_get_team_id_map(api):
    payload = {"teams": [{"id": 147, "abbreviation": "NYY"}, {"id": None, "abbreviation": "X"}]}
    with patch.object(api.session, "get", return_value=_resp(payload)):
        m = api.get_team_id_map()
    assert m == {147: "NYY"}  # None id dropped


def test_get_roster(api):
    payload = {"roster": [{"person": {"id": 1, "fullName": "A B"}}]}
    with patch.object(api.session, "get", return_value=_resp(payload)) as g:
        roster = api.get_roster(147)
    assert roster[0]["person"]["id"] == 1
    _, kwargs = g.call_args
    assert kwargs["params"]["rosterType"] == "active"


def test_get_schedule_flattens_dates(api):
    payload = {
        "dates": [
            {"games": [{"gamePk": 1}, {"gamePk": 2}]},
            {"games": [{"gamePk": 3}]},
        ]
    }
    with patch.object(api.session, "get", return_value=_resp(payload)) as g:
        games = api.get_schedule("2024-07-01")
    assert [x["gamePk"] for x in games] == [1, 2, 3]
    _, kwargs = g.call_args
    assert kwargs["params"]["date"] == "2024-07-01"
    assert "probablePitcher" in kwargs["params"]["hydrate"]


def test_get_schedule_empty(api):
    with patch.object(api.session, "get", return_value=_resp({})):
        assert api.get_schedule("2024-07-01") == []


def test_get_boxscore(api):
    payload = {"teams": {"home": {"players": {}}, "away": {"players": {}}}}
    with patch.object(api.session, "get", return_value=_resp(payload)) as g:
        box = api.get_boxscore(744914)
    assert "teams" in box
    args, _ = g.call_args
    assert args[0].endswith("/game/744914/boxscore")


def test_get_people(api):
    payload = {
        "people": [
            {"id": 1, "pitchHand": {"code": "L"}, "batSide": {"code": "R"}},
            {"id": 2, "pitchHand": {"code": "R"}, "batSide": {"code": "S"}},
        ]
    }
    with patch.object(api.session, "get", return_value=_resp(payload)) as g:
        people = api.get_people([1, 2])
    assert len(people) == 2
    assert people[0]["pitchHand"]["code"] == "L"
    _, kwargs = g.call_args
    assert kwargs["params"]["personIds"] == "1,2"


def test_get_people_empty_ids_no_request(api):
    with patch.object(api.session, "get") as g:
        assert api.get_people([]) == []
    g.assert_not_called()


def test_get_player_game_log_pitching(api):
    payload = {"stats": [{"splits": [{"date": "2024-04-13", "stat": {"strikeOuts": 6}}]}]}
    with patch.object(api.session, "get", return_value=_resp(payload)) as g:
        splits = api.get_player_game_log(684320, "pitching", 2024)
    assert splits[0]["stat"]["strikeOuts"] == 6
    _, kwargs = g.call_args
    assert kwargs["params"]["group"] == "pitching"
    assert kwargs["params"]["season"] == 2024


def test_get_player_game_log_empty_stats(api):
    with patch.object(api.session, "get", return_value=_resp({"stats": []})):
        assert api.get_player_game_log(1, "hitting", 2024) == []


def test_get_player_game_log_bad_group(api):
    with pytest.raises(ValueError):
        api.get_player_game_log(1, "fielding", 2024)


def test_get_retries_then_succeeds(api):
    good = _resp({"teams": []})
    with (
        patch.object(
            api.session,
            "get",
            side_effect=[requests.RequestException("boom"), good],
        ) as g,
        patch("mlb.scripts.utilities.statsapi_wrapper.time.sleep"),
    ):
        teams = api.get_teams()
    assert teams == []
    assert g.call_count == 2


def test_get_raises_after_max_retries(api):
    with (
        patch.object(api.session, "get", side_effect=requests.RequestException("down")),
        patch("mlb.scripts.utilities.statsapi_wrapper.time.sleep"),
    ):
        with pytest.raises(requests.RequestException):
            api._get("/teams", max_retries=3)


def test_get_current_season():
    from datetime import datetime
    from zoneinfo import ZoneInfo

    fixed = datetime(2025, 6, 16, tzinfo=ZoneInfo("America/New_York"))
    assert get_current_season(now=fixed) == 2025
