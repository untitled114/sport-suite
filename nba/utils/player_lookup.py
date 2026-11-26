"""Player name to NBA player id, through the database's own ``players.name_key``.

One rule for every loader that files a row under a player: the name a feed gives is resolved to
the stored profile, and to exactly one profile when two share a name.

The key is ``players.name_key(text)``: lower case, accents and punctuation removed, a trailing
Jr/Sr/II/III/IV dropped, spaces removed ("Jabari Smith Jr." -> "jabarismith"). It is computed in
SQL, never re-implemented in Python.

Two profiles can share a key (Brandon Williams 1585 and 1630314; Gary Trent and Gary Trent Jr.).
The current player is the profile with ``team_abbrev`` set: only
``load_nba_players.load_current_season_profiles`` (the NBA stats CommonAllPlayers list for the
current season) writes it, and every other profile has NULL. When that doesn't separate them, the
profile with game logs in the
season of ``on_date`` (and the other without) is the one. Otherwise the name resolves to None,
with a log line, and the row is filed with a NULL player id; never a guess.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from typing import Any, Dict, Iterable, List, Optional, Set

from nba.utils.season import DateLike, season_for_date

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Profile:
    player_id: int
    full_name: str
    current: bool  # team_abbrev is set


class PlayerLookup:
    """Resolves names to player ids. Loads every profile and each player's log seasons once."""

    def __init__(self, conn: Any) -> None:
        self.conn = conn
        self._by_key: Dict[str, List[Profile]] = defaultdict(list)
        self._seasons: Dict[int, Set[int]] = defaultdict(set)
        self.latest_log: Dict[int, date] = {}
        self._keys: Dict[str, Optional[str]] = {}
        self._warned: Set[str] = set()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT player_id, full_name, players.name_key(full_name), team_abbrev IS NOT NULL "
                "FROM players.player_profile"
            )
            for player_id, full_name, key, current in cur.fetchall():
                self._by_key[key].append(Profile(player_id, full_name, current))
            cur.execute(
                "SELECT player_id, season, MAX(game_date) FROM players.player_game_logs "
                "GROUP BY player_id, season"
            )
            for player_id, season, latest in cur.fetchall():
                self._seasons[player_id].add(season)
                if player_id not in self.latest_log or latest > self.latest_log[player_id]:
                    self.latest_log[player_id] = latest

    def prefetch(self, names: Iterable[str]) -> None:
        """Compute the keys of many names in one query (the loaders call it per file)."""
        todo = sorted({n for n in names if n and n not in self._keys})
        if not todo:
            return
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT n, players.name_key(n) FROM unnest(%s::text[]) AS t(n)",
                (todo,),
            )
            self._keys.update(dict(cur.fetchall()))

    def key(self, name: str) -> Optional[str]:
        """``players.name_key(name)``, cached."""
        if name not in self._keys:
            self.prefetch([name])
        return self._keys.get(name)

    def resolve(self, name: str, on_date: Optional[DateLike] = None) -> Optional[int]:
        """The NBA player id for ``name`` (on ``on_date``, for names two profiles share)."""
        if not name:
            return None
        candidates = self._by_key.get(self.key(name) or "", [])
        if len(candidates) == 1:
            return candidates[0].player_id
        current = [p for p in candidates if p.current]
        if len(current) == 1:
            return current[0].player_id
        pool = current or candidates
        if pool and on_date is not None:
            season = season_for_date(on_date)
            playing = [p for p in pool if season in self._seasons.get(p.player_id, ())]
            if len(playing) == 1:
                return playing[0].player_id
        self._warn(name, candidates, on_date)
        return None

    def _warn(self, name: str, candidates: List[Profile], on_date: Optional[DateLike]) -> None:
        if name in self._warned:
            return
        self._warned.add(name)
        if not candidates:
            logger.warning("player_lookup: no profile for %r (key %r)", name, self.key(name))
        else:
            ids = [p.player_id for p in candidates]
            logger.warning(
                "player_lookup: %r matches profiles %s and none is current or alone in the "
                "season of %s; filed with no player id",
                name,
                ids,
                on_date,
            )
