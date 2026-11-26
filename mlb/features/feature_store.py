"""
Feature Store ETL (MLB)
=======================
Writes computed feature vectors to the ``mlb_features`` schema and reads them
back for training and inference. Decouples feature computation from model
consumption.

Mirrors ``nba/features/feature_store.py``; uses the same database with
``search_path=mlb_features`` via ``mlb.config.database.get_features_db_config``.

Usage:
    store = FeatureStore()

    # Write after extraction
    store.write_features("Gerrit Cole", "2026-04-10", "STRIKEOUTS", "mlb_v1", feats)

    # Read for training
    rows = store.read_features("mlb_v1", start_date="2024-03-01", end_date="2026-10-01")

    # Read for inference (single player)
    feats = store.read_player_features("Gerrit Cole", "2026-04-10", "STRIKEOUTS", "mlb_v1")
"""

import json
import logging
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Union

import psycopg2

from mlb.config.database import get_features_db_config
from mlb.sql import named_query

logger = logging.getLogger(__name__)


class FeatureStore:
    """Read/write interface for the ``mlb_features`` schema."""

    def __init__(self, conn=None):
        """
        Args:
            conn: Optional psycopg2 connection. If None, creates one from config.
        """
        self._external_conn = conn is not None
        self._conn = conn

    def _get_conn(self):
        if self._conn is None or self._conn.closed:
            self._conn = psycopg2.connect(**get_features_db_config())
            self._conn.autocommit = True
        return self._conn

    def close(self):
        if self._conn and not self._external_conn and not self._conn.closed:
            self._conn.close()

    # --------------------------------------------------------------
    # WRITE
    # --------------------------------------------------------------

    def write_features(
        self,
        player_name: str,
        game_date: Union[str, date],
        stat_type: str,
        feature_set_name: str,
        feature_values: Dict[str, Any],
        player_id: Optional[int] = None,
    ) -> bool:
        """
        Write a single feature vector to the store.

        Uses upsert - overwrites if (player, date, stat type, feature_set) exists.

        Returns:
            True if successful, False on error.
        """
        if not feature_values:
            logger.warning(
                "Empty feature_values for %s/%s/%s - skipping",
                player_name,
                game_date,
                stat_type,
            )
            return False

        game_date_str = str(game_date)

        try:
            conn = self._get_conn()
            with conn.cursor() as cur:
                cur.execute(
                    named_query("computed_features_upsert"),
                    (
                        player_name,
                        player_id,
                        game_date_str,
                        stat_type,
                        feature_set_name,
                        json.dumps(feature_values, default=_json_default),
                    ),
                )
            return True
        except Exception:
            logger.exception(
                "Failed to write features for %s/%s/%s", player_name, game_date, stat_type
            )
            return False

    def write_batch(
        self,
        rows: List[Dict[str, Any]],
        feature_set_name: str,
    ) -> int:
        """
        Bulk-write feature vectors.

        Each row dict must contain: player_name, game_date, stat_type,
        feature_values. Optional: player_id.

        Returns:
            Number of rows successfully written.
        """
        if not rows:
            return 0

        written = 0
        try:
            conn = self._get_conn()
            with conn.cursor() as cur:
                for row in rows:
                    player_name = row["player_name"]
                    game_date = str(row["game_date"])
                    stat_type = row["stat_type"]
                    features = row["feature_values"]
                    player_id = row.get("player_id")

                    if not features:
                        continue

                    cur.execute(
                        named_query("computed_features_upsert"),
                        (
                            player_name,
                            player_id,
                            game_date,
                            stat_type,
                            feature_set_name,
                            json.dumps(features, default=_json_default),
                        ),
                    )
                    written += 1
        except Exception:
            logger.exception("Batch write failed after %d rows", written)

        return written

    # --------------------------------------------------------------
    # READ
    # --------------------------------------------------------------

    def read_player_features(
        self,
        player_name: str,
        game_date: Union[str, date],
        stat_type: str,
        feature_set_name: str,
    ) -> Optional[Dict[str, Any]]:
        """Read a single feature vector. Returns None if not found."""
        try:
            conn = self._get_conn()
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT feature_values FROM computed_features
                    WHERE player_name = %s AND game_date = %s
                      AND stat_type = %s AND feature_set_name = %s
                    """,
                    (player_name, str(game_date), stat_type, feature_set_name),
                )
                row = cur.fetchone()
                if row:
                    val = row[0]
                    return json.loads(val) if isinstance(val, str) else val
                return None
        except Exception:
            logger.exception(
                "Failed to read features for %s/%s/%s", player_name, game_date, stat_type
            )
            return None

    def read_features(
        self,
        feature_set_name: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        stat_type: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """
        Read feature vectors for training. Returns list of dicts with
        player_name, game_date, stat type, and all feature values flattened.
        """
        conditions = ["feature_set_name = %s"]
        params: list = [feature_set_name]

        if start_date:
            conditions.append("game_date >= %s")
            params.append(start_date)
        if end_date:
            conditions.append("game_date <= %s")
            params.append(end_date)
        if stat_type:
            conditions.append("stat_type = %s")
            params.append(stat_type)

        where = " AND ".join(conditions)
        query = (
            "SELECT player_name, game_date, stat_type, feature_values "
            f"FROM computed_features WHERE {where} ORDER BY game_date"
        )

        if limit:
            query += " LIMIT %s"
            params.append(limit)

        try:
            conn = self._get_conn()
            with conn.cursor() as cur:
                cur.execute(query, params)
                results = []
                for row in cur.fetchall():
                    fv = row[3]
                    features = json.loads(fv) if isinstance(fv, str) else fv
                    features["player_name"] = row[0]
                    features["game_date"] = str(row[1])
                    features["stat_type"] = row[2]
                    results.append(features)
                return results
        except Exception:
            logger.exception("Failed to read features for %s", feature_set_name)
            return []

    # --------------------------------------------------------------
    # METADATA
    # --------------------------------------------------------------

    def get_feature_set(self, name: str) -> Optional[Dict[str, Any]]:
        """Get feature set definition by name."""
        try:
            conn = self._get_conn()
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT name, version, feature_count, feature_names, "
                    "description, is_active FROM feature_sets WHERE name = %s",
                    (name,),
                )
                row = cur.fetchone()
                if row:
                    return {
                        "name": row[0],
                        "version": row[1],
                        "feature_count": row[2],
                        "feature_names": row[3],
                        "description": row[4],
                        "is_active": row[5],
                    }
                return None
        except Exception:
            logger.exception("Failed to get feature set %s", name)
            return None

    def list_feature_sets(self, active_only: bool = True) -> List[Dict[str, Any]]:
        """List all feature sets."""
        try:
            conn = self._get_conn()
            with conn.cursor() as cur:
                query = "SELECT name, version, feature_count, is_active FROM feature_sets"
                if active_only:
                    query += " WHERE is_active = TRUE"
                query += " ORDER BY name"
                cur.execute(query)
                return [
                    {"name": r[0], "version": r[1], "feature_count": r[2], "is_active": r[3]}
                    for r in cur.fetchall()
                ]
        except Exception:
            logger.exception("Failed to list feature sets")
            return []

    def get_coverage(
        self, feature_set_name: str, stat_type: Optional[str] = None
    ) -> Dict[str, Any]:
        """Get date range and count of stored features."""
        conditions = ["feature_set_name = %s"]
        params: list = [feature_set_name]
        if stat_type:
            conditions.append("stat_type = %s")
            params.append(stat_type)

        where = " AND ".join(conditions)

        try:
            conn = self._get_conn()
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*), MIN(game_date), MAX(game_date) "
                    f"FROM computed_features WHERE {where}",
                    params,
                )
                row = cur.fetchone()
                return {
                    "count": row[0] or 0,
                    "min_date": str(row[1]) if row[1] else None,
                    "max_date": str(row[2]) if row[2] else None,
                }
        except Exception:
            logger.exception("Failed to get coverage")
            return {"count": 0, "min_date": None, "max_date": None}

    def update_feature_metadata(
        self,
        feature_set_name: str,
        feature_name: str,
        train_mean: Optional[float] = None,
        train_std: Optional[float] = None,
        train_min: Optional[float] = None,
        train_max: Optional[float] = None,
        shap_importance: Optional[float] = None,
        importance_rank: Optional[int] = None,
    ) -> bool:
        """Upsert feature metadata (drift baseline, importance)."""
        try:
            conn = self._get_conn()
            with conn.cursor() as cur:
                cur.execute(
                    named_query("feature_metadata_upsert"),
                    (
                        feature_set_name,
                        feature_name,
                        train_mean,
                        train_std,
                        train_min,
                        train_max,
                        shap_importance,
                        importance_rank,
                    ),
                )
            return True
        except Exception:
            logger.exception("Failed to update metadata for %s/%s", feature_set_name, feature_name)
            return False


def _json_default(obj):
    """JSON serializer for numpy/datetime types."""
    import numpy as np

    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return float(obj)
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    if isinstance(obj, (datetime, date)):
        return str(obj)
    if isinstance(obj, np.bool_):
        return bool(obj)
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")
