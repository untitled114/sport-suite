"""
MLB Stats API Wrapper
=====================
Thin client for the public MLB Stats API (https://statsapi.mlb.com/api/v1).
No API key required. Mirrors the role of ``nba/scripts/utilities/nba_api_wrapper.py``
but speaks raw JSON over ``requests`` instead of the ``nba_api`` package.

Endpoints covered:
    - /schedule?sportId=1&date=YYYY-MM-DD&hydrate=probablePitcher,team,venue
    - /game/{gamePk}/boxscore
    - /people/{id}/stats?stats=gameLog&group=hitting|pitching&season=YYYY
    - /teams?sportId=1
    - /teams/{id}/roster?rosterType=active

Rate-limit hygiene: small sleeps between calls + exponential-backoff retries.
Encoding: ``Accept-Encoding: gzip, deflate`` ONLY - never include brotli (``br``),
which silently breaks decompression (a known failure mode). EST dates.

Usage:
    from mlb.scripts.utilities.statsapi_wrapper import StatsAPIWrapper

    api = StatsAPIWrapper()
    teams = api.get_teams()
    games = api.get_schedule("2024-07-01")
    box = api.get_boxscore(744914)
    logs = api.get_player_game_log(684320, "pitching", 2024)
"""

import logging
import time
from typing import Any, Dict, List, Optional

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

BASE_URL = "https://statsapi.mlb.com/api/v1"

# NEVER add brotli ("br") - it silently breaks decompression in requests.
DEFAULT_HEADERS = {
    "Accept": "application/json",
    "Accept-Encoding": "gzip, deflate",
    "User-Agent": "sport-suite-mlb/1.0",
}


class StatsAPIWrapper:
    """Robust client for the MLB Stats API with retries and rate limiting."""

    def __init__(self, requests_per_minute: int = 60, base_url: str = BASE_URL):
        """
        Args:
            requests_per_minute: Soft rate limit for outbound calls (default 60).
            base_url: API root (override only for testing).
        """
        self.base_url = base_url.rstrip("/")
        self.requests_per_minute = requests_per_minute
        self.min_delay = 60.0 / requests_per_minute if requests_per_minute > 0 else 0.0
        self.last_request_time = 0.0
        self.session = requests.Session()
        self.session.headers.update(DEFAULT_HEADERS)
        logger.info(
            f"Initialized MLB Stats API wrapper (rate limit: {requests_per_minute} req/min)"
        )

    def _rate_limit(self):
        """Sleep just enough to honor the configured request rate."""
        if self.min_delay <= 0:
            return
        elapsed = time.time() - self.last_request_time
        if elapsed < self.min_delay:
            time.sleep(self.min_delay - elapsed)
        self.last_request_time = time.time()

    def _get(
        self, path: str, params: Optional[Dict[str, Any]] = None, max_retries: int = 4
    ) -> Dict[str, Any]:
        """
        GET a JSON endpoint with exponential-backoff retries.

        Args:
            path: Path relative to base URL (e.g. "/schedule").
            params: Query string parameters.
            max_retries: Maximum attempts before raising.

        Returns:
            Parsed JSON dict (empty dict on persistent failure-but-no-raise paths).
        """
        url = f"{self.base_url}{path}"
        last_exc: Optional[Exception] = None

        for attempt in range(max_retries):
            try:
                self._rate_limit()
                resp = self.session.get(url, params=params, timeout=30)
                resp.raise_for_status()
                return resp.json()
            except (requests.RequestException, ValueError) as e:
                last_exc = e
                wait = 2**attempt
                logger.warning(f"GET {path} failed (attempt {attempt + 1}/{max_retries}): {e}")
                if attempt < max_retries - 1:
                    time.sleep(wait)

        logger.error(f"GET {path} failed after {max_retries} attempts")
        raise last_exc if last_exc else RuntimeError(f"GET {path} failed")

    # ========== TEAMS ==========

    def get_teams(self, sport_id: int = 1) -> List[Dict[str, Any]]:
        """
        Fetch all MLB teams (30 for sportId=1).

        Returns:
            List of team dicts (id, abbreviation, name, division, league, venue, ...).
        """
        data = self._get("/teams", params={"sportId": sport_id})
        return data.get("teams", [])

    def get_team_id_map(self, sport_id: int = 1) -> Dict[int, str]:
        """Build a ``{team_id: abbreviation}`` map (used to resolve game-log teams)."""
        return {
            t["id"]: t.get("abbreviation")
            for t in self.get_teams(sport_id=sport_id)
            if t.get("id") is not None
        }

    def get_roster(self, team_id: int, roster_type: str = "active") -> List[Dict[str, Any]]:
        """
        Fetch a team's roster.

        Args:
            team_id: MLB team id.
            roster_type: "active" (default), "fullSeason", "40Man", etc.

        Returns:
            List of roster entries (person, position, status, jerseyNumber).
        """
        data = self._get(f"/teams/{team_id}/roster", params={"rosterType": roster_type})
        return data.get("roster", [])

    # ========== SCHEDULE / GAMES ==========

    def get_schedule(
        self,
        date: str,
        sport_id: int = 1,
        hydrate: str = "probablePitcher,team,venue",
    ) -> List[Dict[str, Any]]:
        """
        Fetch the schedule (with probable pitchers) for a single EST date.

        Args:
            date: "YYYY-MM-DD".
            sport_id: 1 = MLB.
            hydrate: Hydration directives (probablePitcher needed for probables).

        Returns:
            Flat list of game dicts for that date (empty if no games).
        """
        data = self._get(
            "/schedule",
            params={"sportId": sport_id, "date": date, "hydrate": hydrate},
        )
        games: List[Dict[str, Any]] = []
        for d in data.get("dates", []):
            games.extend(d.get("games", []))
        return games

    # ========== PEOPLE ==========

    def get_people(self, player_ids: List[int]) -> List[Dict[str, Any]]:
        """
        Fetch person records (incl. handedness) for one or more player ids.

        The ``/people`` endpoint accepts a comma-separated id list, so a whole
        batch is fetched in a single request. Each person carries ``pitchHand``
        and ``batSide`` sub-objects (``{"code": "L"|"R"|"S"}``).

        Args:
            player_ids: MLBAM person ids.

        Returns:
            List of person dicts (empty if ``player_ids`` is empty).
        """
        if not player_ids:
            return []
        ids = ",".join(str(pid) for pid in player_ids)
        data = self._get("/people", params={"personIds": ids})
        return data.get("people", [])

    def get_boxscore(self, game_pk: int) -> Dict[str, Any]:
        """
        Fetch the boxscore for a game.

        Returns:
            The raw boxscore dict; ``["teams"]["home"|"away"]["players"]`` holds
            per-player batting/pitching stat lines.
        """
        return self._get(f"/game/{game_pk}/boxscore")

    # ========== PLAYER GAME LOGS ==========

    def get_player_game_log(self, player_id: int, group: str, season: int) -> List[Dict[str, Any]]:
        """
        Fetch a player's per-game log for a season.

        Args:
            player_id: MLB person id.
            group: "hitting" or "pitching".
            season: 4-digit season year.

        Returns:
            List of "splits" dicts (one per game). Each split has ``date``,
            ``game``, ``team``, ``opponent``, ``isHome`` and a ``stat`` payload.
        """
        if group not in ("hitting", "pitching"):
            raise ValueError(f"group must be 'hitting' or 'pitching', got {group!r}")
        data = self._get(
            f"/people/{player_id}/stats",
            params={"stats": "gameLog", "group": group, "season": season},
        )
        stats = data.get("stats", [])
        if not stats:
            return []
        return stats[0].get("splits", []) or []


def get_current_season(now=None) -> int:
    """
    Return the current MLB season year (EST). MLB seasons run Feb-Nov within a
    single calendar year, so the season is simply the current year.
    """
    from datetime import datetime
    from zoneinfo import ZoneInfo

    now = now or datetime.now(ZoneInfo("America/New_York"))
    return now.year


if __name__ == "__main__":
    api = StatsAPIWrapper()

    print("1. Teams...")
    teams = api.get_teams()
    print(f"   Found {len(teams)} teams")

    print("2. Today schedule...")
    from datetime import datetime
    from zoneinfo import ZoneInfo

    today = datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
    games = api.get_schedule(today)
    print(f"   {len(games)} games on {today}")

    print("MLB Stats API Wrapper smoke test complete!")
