-- Register a feature set: its name, version, and the ordered list of features it holds.
--
-- Feature vectors refer to their set by name (a foreign key), so a set is registered before its
-- first vector is written. Registering again replaces the list, count and description.
--
-- Parameters, in order:
--   1. name
--   2. version
--   3. feature count
--   4. feature names (text array, in column order)
--   5. description
-- Run by: nba/features/feature_store.py (register_feature_set)
INSERT INTO feature_sets (name, version, feature_count, feature_names, description)
VALUES (%s, %s, %s, %s, %s)
ON CONFLICT (name) DO UPDATE SET
    version = EXCLUDED.version,
    feature_count = EXCLUDED.feature_count,
    feature_names = EXCLUDED.feature_names,
    description = EXCLUDED.description;
