#!/usr/bin/env python3
"""
Player Name Normalizer (MLB)
============================
Normalises a player name in Python (case, accents, punctuation, suffixes) and resolves it to
a player id, for loaders and feature extractors that are given a name.

MLB has TWO player roles (pitchers and batters) stored in SEPARATE profile
tables (``pitcher_profile`` and ``batter_profile`` in the ``mlb_players``
schema). The normalizer can resolve a name against either or both tables.

Usage:
    from mlb.utils.name_normalizer import NameNormalizer

    normalizer = NameNormalizer()
    player_id = normalizer.get_canonical_player_id("José Ramírez")
    normalized = NameNormalizer.normalize_name("Shohei Ohtani")

Mirrors ``nba/utils/name_normalizer.py``. Adapted for baseball's two-table
player model.
"""

import pickle
import unicodedata
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

EST = ZoneInfo("America/New_York")


class NameNormalizer:
    """
    Universal MLB player name normalizer with caching.

    Handles:
    - Accented characters (Ramírez -> Ramirez)
    - Suffixes (Jr., Sr., II, III, IV)
    - Periods in initials (J.D. -> JD)
    - Duplicate player_ids (keeps the one with most game logs)
    - Both pitcher and batter profile tables
    - Caching for performance
    """

    # Nickname mappings: full name -> common name used by the data provider.
    # Applied AFTER base normalization. Left empty by default; populate as
    # mismatches are discovered against other feeds' naming.
    NICKNAME_MAP: Dict[str, str] = {}

    # Database config - the mlb_players schema
    @staticmethod
    def _get_db_config():
        from mlb.config.database import get_schema_config

        return get_schema_config("mlb_players")

    # Cache file location (kept alongside this module)
    CACHE_FILE = Path(__file__).parent / ".mlb_name_normalizer_cache.pkl"
    CACHE_MAX_AGE_HOURS = 24  # Rebuild cache daily

    # (profile_table, game_logs_table) pairs scanned to build the mapping.
    PROFILE_SOURCES = (
        ("pitcher_profile", "pitcher_game_logs"),
        ("batter_profile", "batter_game_logs"),
    )

    def __init__(self, use_cache: bool = True):
        """
        Initialize the name normalizer.

        Args:
            use_cache: Use cached mapping if available (default: True)
        """
        self.use_cache = use_cache
        self.mapping: Dict[str, Dict] = {}  # normalized_name -> info
        self.reverse_mapping: Dict[str, str] = {}  # original_name -> normalized

        self._load_mapping()

    @staticmethod
    def normalize_name(name: str) -> str:
        """
        Normalize a player name to its canonical form.

        Transformations:
        1. Remove periods from initials (J.D. -> JD)
        2. Convert to title case
        3. Fix common initials broken by title case (JD, AJ, etc.)
        4. Remove accents (Ramírez -> Ramirez)
        5. Remove suffixes (Jr., Sr., II, III, IV)
        6. Normalize whitespace
        7. Apply nickname mappings

        Examples:
            >>> NameNormalizer.normalize_name("José Ramírez")
            'Jose Ramirez'
            >>> NameNormalizer.normalize_name("shohei ohtani")
            'Shohei Ohtani'
            >>> NameNormalizer.normalize_name("J.D. Martinez")
            'JD Martinez'
            >>> NameNormalizer.normalize_name("Ronald Acuña Jr.")
            'Ronald Acuna'

        Args:
            name: Player name

        Returns:
            Normalized name
        """
        if not name:
            return ""

        # Remove periods first (before title casing to preserve initials)
        name = name.strip().replace(".", "")

        # Convert to title case
        name = name.title()

        # Fix common initials that .title() breaks (JD, AJ, CJ, etc.)
        initials = ["Jd ", "Aj ", "Cj ", "Tj ", "Jp ", "Cc ", "Dj ", "Jt "]
        for initial in initials:
            if name.startswith(initial):
                name = initial.upper() + name[3:]

        # Fix mixed-case names that .title() breaks (DeJong, LeMahieu, etc.)
        mixed_case_fixes = {
            "Dejong": "DeJong",
            "Lemahieu": "LeMahieu",
            "Mccutchen": "McCutchen",
            "Mcmahon": "McMahon",
            "Mcclanahan": "McClanahan",
            "Mckenzie": "McKenzie",
            "Degrom": "deGrom",
        }
        for wrong, correct in mixed_case_fixes.items():
            if wrong in name:
                name = name.replace(wrong, correct)

        # Remove accents (í -> i, ñ -> n, etc.)
        nfd = unicodedata.normalize("NFD", name)
        name = "".join(char for char in nfd if unicodedata.category(char) != "Mn")

        # Remove suffixes (Jr, Sr, II, III, IV, V)
        suffixes = [" Jr", " Sr", " Ii", " Iii", " Iv", " V"]
        for suffix in suffixes:
            if name.endswith(suffix):
                name = name[: -len(suffix)]
                break

        # Normalize whitespace
        name = " ".join(name.split()).strip()

        # Apply nickname mappings
        if name in NameNormalizer.NICKNAME_MAP:
            name = NameNormalizer.NICKNAME_MAP[name]

        return name

    def _load_mapping(self):
        """Load the player name mapping from cache or rebuild from the database."""
        if self.use_cache and self._load_from_cache():
            return

        print("Building MLB name normalization mapping from database...")
        self._build_from_database()

        if self.use_cache:
            self._save_to_cache()

    def _load_from_cache(self) -> bool:
        """Load mapping from the cache file if fresh enough."""
        if not self.CACHE_FILE.exists():
            return False

        try:
            cache_age = datetime.now(EST) - datetime.fromtimestamp(
                self.CACHE_FILE.stat().st_mtime, tz=EST
            )
            if cache_age > timedelta(hours=self.CACHE_MAX_AGE_HOURS):
                print(f"Cache is {cache_age.total_seconds()/3600:.1f}h old, rebuilding...")
                return False

            with open(self.CACHE_FILE, "rb") as f:
                data = pickle.load(f)

            self.mapping = data["mapping"]
            self.reverse_mapping = data["reverse_mapping"]
            print(f"Loaded MLB name mapping from cache ({len(self.mapping):,} names)")
            return True

        except (KeyError, ValueError, TypeError) as e:
            print(f"Error loading cache: {e}, rebuilding...")
            return False

    def _save_to_cache(self):
        """Save the mapping to the cache file."""
        try:
            with open(self.CACHE_FILE, "wb") as f:
                pickle.dump(
                    {
                        "mapping": self.mapping,
                        "reverse_mapping": self.reverse_mapping,
                        "created_at": datetime.now(EST).isoformat(),
                    },
                    f,
                )
            print("Saved MLB name mapping to cache")
        except (KeyError, ValueError, TypeError) as e:
            print(f"Warning: Could not save cache: {e}")

    def _build_from_database(self):
        """
        Build the name mapping from PostgreSQL across both profile tables.

        For each normalized name, find all (player_id, player_type) entries and
        keep the one with the MOST game logs as canonical.
        """
        from collections import defaultdict

        import psycopg2

        conn = psycopg2.connect(**self._get_db_config())
        conn.autocommit = True
        cursor = conn.cursor()

        name_groups = defaultdict(list)
        total_rows = 0

        try:
            for profile_table, logs_table, player_type in (
                ("pitcher_profile", "pitcher_game_logs", "pitcher"),
                ("batter_profile", "batter_game_logs", "batter"),
            ):
                query = f"""
                SELECT
                    p.full_name,
                    p.player_id,
                    COALESCE(g.game_count, 0) AS game_count
                FROM {profile_table} p
                LEFT JOIN (
                    SELECT player_id, COUNT(*) AS game_count
                    FROM {logs_table}
                    GROUP BY player_id
                ) g ON p.player_id = g.player_id
                ORDER BY p.full_name, game_count DESC
                """
                cursor.execute(query)
                rows = cursor.fetchall()
                total_rows += len(rows)

                for full_name, player_id, game_count in rows:
                    normalized = self.normalize_name(full_name)
                    name_groups[normalized].append(
                        {
                            "original_name": full_name,
                            "player_id": player_id,
                            "game_count": game_count,
                            "player_type": player_type,
                        }
                    )

            for normalized_name, players in name_groups.items():
                players_sorted = sorted(players, key=lambda p: p["game_count"], reverse=True)
                canonical = players_sorted[0]
                self.mapping[normalized_name] = {
                    "player_id": canonical["player_id"],
                    "player_type": canonical["player_type"],
                    "game_count": canonical["game_count"],
                    "original_names": [p["original_name"] for p in players],
                    "all_player_ids": [p["player_id"] for p in players],
                    "has_duplicates": len(players) > 1,
                }
                for player in players:
                    self.reverse_mapping[player["original_name"]] = normalized_name

            print(f"Built mapping for {len(self.mapping):,} names from {total_rows:,} entries")

        finally:
            cursor.close()
            conn.close()

    def get_canonical_player_id(self, player_name: str) -> Optional[int]:
        """
        Get the canonical player_id for a player name.

        Args:
            player_name: Player name (any variation)

        Returns:
            Canonical player_id or None if not found
        """
        normalized = self.normalize_name(player_name)
        if normalized in self.mapping:
            return self.mapping[normalized]["player_id"]
        return None

    def get_player_info(self, player_name: str) -> Optional[Dict]:
        """
        Get full player information (id, type, game count, variations).

        Args:
            player_name: Player name

        Returns:
            Dict with player info or None
        """
        normalized = self.normalize_name(player_name)
        return self.mapping.get(normalized)

    def get_player_type(self, player_name: str) -> Optional[str]:
        """Return 'pitcher' or 'batter' for a player name, or None if unknown."""
        info = self.get_player_info(player_name)
        return info["player_type"] if info else None

    def has_duplicates(self, player_name: str) -> bool:
        """Check if a player name has duplicate player_ids in the database."""
        info = self.get_player_info(player_name)
        return info["has_duplicates"] if info else False

    def get_all_variations(self, player_name: str) -> List[str]:
        """Get all name variations for a player."""
        info = self.get_player_info(player_name)
        return info["original_names"] if info else []

    def rebuild_cache(self):
        """Force a rebuild of the cache from the database."""
        print("Forcing MLB cache rebuild...")
        self._build_from_database()
        if self.use_cache:
            self._save_to_cache()


# Global singleton instance
_normalizer_instance = None


def get_normalizer(use_cache: bool = True, rebuild: bool = False) -> NameNormalizer:
    """
    Get the global MLB NameNormalizer singleton instance.

    Args:
        use_cache: Use cached mapping (default: True)
        rebuild: Force rebuild from database (default: False)

    Returns:
        NameNormalizer instance
    """
    global _normalizer_instance

    if _normalizer_instance is None or rebuild:
        _normalizer_instance = NameNormalizer(use_cache=use_cache)
        if rebuild:
            _normalizer_instance.rebuild_cache()

    return _normalizer_instance


# Convenience functions for direct use
def normalize_name(name: str) -> str:
    """Normalize a player name (static method, no database required)."""
    return NameNormalizer.normalize_name(name)


def get_canonical_player_id(player_name: str) -> Optional[int]:
    """Get canonical player_id for a player name."""
    return get_normalizer().get_canonical_player_id(player_name)


def get_player_info(player_name: str) -> Optional[Dict]:
    """Get full player information."""
    return get_normalizer().get_player_info(player_name)
