"""
Base Feature Extractor (MLB)
============================
Abstract base class for all MLB feature extractors.

Mirrors ``nba/features/extractors/base.py``. Provides common functionality for
database access, logging, date normalization, and default value handling.

Baseball has TWO player roles (pitchers and batters), so the abstract
``extract()`` signature uses ``player_name`` + ``stat_type`` and accepts arbitrary
keyword context (opponent, probable pitcher, handedness, park factor, etc.).
"""

import logging
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

import pandas as pd

logger = logging.getLogger(__name__)


class BaseFeatureExtractor(ABC):
    """
    Abstract base class for MLB feature extractors.

    All feature extractors should inherit from this class and implement:
    - extract(): Main extraction method
    - get_defaults(): Returns default values for all features
    - FEATURE_NAMES: Class attribute listing all feature names

    Attributes:
        conn: Database connection (psycopg2 or similar)
        name: Extractor name for logging
    """

    # Subclasses must define this
    FEATURE_NAMES: tuple = ()

    def __init__(self, conn: Any, name: Optional[str] = None):
        """
        Initialize the feature extractor.

        Args:
            conn: Database connection object (psycopg2)
            name: Optional extractor name for logging (defaults to class name)
        """
        self.conn = conn
        self.name = name or self.__class__.__name__

    @abstractmethod
    def extract(
        self,
        player_name: str,
        game_date: Any,
        stat_type: str,
        **kwargs,
    ) -> Dict[str, float]:
        """
        Extract features for a player/game/stat type combination.

        Args:
            player_name: Player's full name (pitcher or batter)
            game_date: Game date (datetime or string)
            stat_type: One of STRIKEOUTS, HITS, TOTAL_BASES
            **kwargs: Additional context (opponent, probable_pitcher, is_home,
                      park_factor, pitcher_hand, etc.)

        Returns:
            Dict mapping feature names to float values
        """
        raise NotImplementedError

    @classmethod
    @abstractmethod
    def get_defaults(cls) -> Dict[str, float]:
        """
        Get default values for all features.

        Returns:
            Dict mapping feature names to default float values
        """
        raise NotImplementedError

    def _safe_query(
        self,
        query: str,
        params: tuple,
        conn: Any = None,
    ) -> Optional[pd.DataFrame]:
        """
        Execute a query with error handling.

        Args:
            query: SQL query string
            params: Query parameters
            conn: Optional connection override (defaults to self.conn)

        Returns:
            DataFrame with results (non-empty), or None on error / no rows
        """
        target = conn if conn is not None else self.conn
        try:
            df = pd.read_sql_query(query, target, params=params)
            return df if len(df) > 0 else None
        except (pd.errors.DatabaseError, ValueError, TypeError) as e:
            logger.debug("%s: Query failed: %s", self.name, e)
            return None

    def _normalize_date(self, game_date: Any) -> str:
        """
        Normalize game_date to string format YYYY-MM-DD.

        Args:
            game_date: Date as string, datetime, or pd.Timestamp

        Returns:
            Date string in YYYY-MM-DD format
        """
        if isinstance(game_date, str):
            return game_date[:10]  # Handle 'YYYY-MM-DD HH:MM:SS' format
        if hasattr(game_date, "strftime"):
            return game_date.strftime("%Y-%m-%d")
        return str(game_date)[:10]

    def _safe_float(self, value: Any, default: float = 0.0) -> float:
        """
        Safely convert value to float.

        Args:
            value: Value to convert
            default: Default if conversion fails

        Returns:
            Float value or default
        """
        if value is None:
            return default
        try:
            result = float(value)
            if pd.isna(result):
                return default
            return result
        except (ValueError, TypeError):
            return default

    def validate_features(self, features: Dict[str, float]) -> Dict[str, float]:
        """
        Validate extracted features and fill missing with defaults.

        Args:
            features: Extracted feature dict

        Returns:
            Validated feature dict with all expected features present
        """
        defaults = self.get_defaults()
        result = defaults.copy()

        for key, value in features.items():
            if key in result:
                result[key] = self._safe_float(value, defaults.get(key, 0.0))

        return result
