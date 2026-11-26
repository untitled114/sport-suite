-- Store one feature vector (insert, or replace on re-run).
--
-- One row per (player, game date, stat type, feature set); the vector is a JSON document.
-- Re-computing a vector replaces it and stamps the time.
--
-- Parameters, in order:
--   1. player name
--   2. player id
--   3. game date
--   4. stat type
--   5. feature set name
--   6. feature values (JSON)
-- Run by: mlb/features/feature_store.py (write_features)
INSERT INTO computed_features
    (player_name, player_id, game_date, stat_type,
     feature_set_name, feature_values, computed_at)
VALUES (%s, %s, %s, %s, %s, %s, NOW())
ON CONFLICT (player_name, game_date, stat_type, feature_set_name)
DO UPDATE SET feature_values = EXCLUDED.feature_values,
              computed_at = NOW();
