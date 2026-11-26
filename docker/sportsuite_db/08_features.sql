-- Feature Store schema
-- Decouples feature computation from both training and inference.
-- Both pipelines read from the same versioned feature store.
SET search_path TO features, public;

-- ==========================================================================
-- FEATURE SETS - versioned definitions of which features exist
-- ==========================================================================
CREATE TABLE feature_sets (
    id SERIAL PRIMARY KEY,
    name VARCHAR(50) NOT NULL UNIQUE,       -- 'base_v1', 'extended_v1', 'wide_v1'
    version INTEGER NOT NULL DEFAULT 1,
    feature_count INTEGER NOT NULL,
    feature_names TEXT[] NOT NULL,           -- ordered list of feature column names
    description TEXT,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- ==========================================================================
-- COMPUTED FEATURES - materialized feature vectors per player/game/date
-- ==========================================================================
CREATE TABLE computed_features (
    id BIGSERIAL PRIMARY KEY,
    player_name VARCHAR(255) NOT NULL,
    player_id INTEGER,
    game_date DATE NOT NULL,
    stat_type VARCHAR(50) NOT NULL,
    feature_set_name VARCHAR(50) NOT NULL REFERENCES feature_sets(name),
    feature_values JSONB NOT NULL,           -- {feature_name: value, ...}
    computed_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (player_name, game_date, stat_type, feature_set_name)
);

CREATE INDEX idx_cf_player_date ON computed_features(player_name, game_date);
CREATE INDEX idx_cf_date ON computed_features(game_date);
CREATE INDEX idx_cf_fset ON computed_features(feature_set_name);

-- ==========================================================================
-- FEATURE METADATA - tracks drift baselines and importance
-- ==========================================================================
CREATE TABLE feature_metadata (
    id SERIAL PRIMARY KEY,
    feature_set_name VARCHAR(50) NOT NULL REFERENCES feature_sets(name),
    feature_name VARCHAR(100) NOT NULL,
    -- Distribution baseline (from training data)
    train_mean DECIMAL(10,4),
    train_std DECIMAL(10,4),
    train_min DECIMAL(10,4),
    train_max DECIMAL(10,4),
    train_median DECIMAL(10,4),
    -- Importance
    shap_importance DECIMAL(8,6),
    importance_rank INTEGER,
    -- Drift monitoring
    last_drift_check TIMESTAMPTZ,
    last_psi DECIMAL(8,4),
    drift_alert BOOLEAN DEFAULT FALSE,
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (feature_set_name, feature_name)
);

CREATE INDEX idx_fm_fset ON feature_metadata(feature_set_name);
CREATE INDEX idx_fm_drift ON feature_metadata(drift_alert) WHERE drift_alert = TRUE;
