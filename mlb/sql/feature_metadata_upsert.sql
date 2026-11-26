-- Store a feature's training statistics and importance (insert, or replace on re-run).
--
-- One row per (feature set, feature). The training mean and spread kept here are what later
-- drift checks compare new data against.
--
-- Parameters, in order:
--   1. one value per column, in the column order of the INSERT
-- Run by: mlb/features/feature_store.py (update_feature_metadata)
INSERT INTO feature_metadata
    (feature_set_name, feature_name, train_mean, train_std,
     train_min, train_max, shap_importance, importance_rank, updated_at)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW())
ON CONFLICT (feature_set_name, feature_name)
DO UPDATE SET
    train_mean = COALESCE(EXCLUDED.train_mean, feature_metadata.train_mean),
    train_std = COALESCE(EXCLUDED.train_std, feature_metadata.train_std),
    train_min = COALESCE(EXCLUDED.train_min, feature_metadata.train_min),
    train_max = COALESCE(EXCLUDED.train_max, feature_metadata.train_max),
    shap_importance = COALESCE(EXCLUDED.shap_importance,
                               feature_metadata.shap_importance),
    importance_rank = COALESCE(EXCLUDED.importance_rank,
                               feature_metadata.importance_rank),
    updated_at = NOW();
