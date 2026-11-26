-- ============================================================================
-- MLB EXPECTED-PA-BY-LINEUP-SLOT SCHEMA EXTENSION
-- ============================================================================
-- Additive DDL for the lineup-slot -> expected-PA curve. Kept separate from the base MLB
-- schema.
--
-- This table holds the curve computed from the stored games by
-- mlb.scripts.loaders.compute_expected_pa:
--   mean_pa(slot) = AVG(batter_game_logs.plate_appearances) over every game a
--   batter started in that lineup slot (batter_lineup_slots JOIN batter_game_logs
--   on player_id+game_id). n_games records the sample size.
-- ============================================================================

CREATE TABLE IF NOT EXISTS mlb_teams.expected_pa_by_slot (
    lineup_slot  INTEGER NOT NULL,        -- 1..9
    window_label TEXT NOT NULL,           -- e.g. 'rolling_2024_2026'
    mean_pa      NUMERIC,                 -- mean plate appearances/game in slot
    n_games      INTEGER NOT NULL DEFAULT 0,
    computed_at  TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (lineup_slot, window_label)
);

COMMENT ON TABLE mlb_teams.expected_pa_by_slot IS
    'Data-derived lineup-slot -> mean plate-appearances/game curve, computed from mlb_players.batter_lineup_slots JOIN batter_game_logs by mlb.scripts.loaders.compute_expected_pa.';
