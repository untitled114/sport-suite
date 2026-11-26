-- ============================================================================
-- MLB SABERMETRIC SCHEMA EXTENSIONS
-- ============================================================================
-- Additive DDL for the SABERMETRIC feature suite. Kept separate from the base MLB schema.
--
-- FIP needs home runs allowed per pitcher-game. The pitcher_game_logs table tracks
-- strikeouts, walks, hits allowed and earned runs but not home runs allowed. This adds
-- that column, backfilled from the MLB Stats API pitching gameLog ``homeRuns`` field.
-- ============================================================================

-- Home runs allowed by the pitcher in the game (FIP/xFIP input).
ALTER TABLE mlb_players.pitcher_game_logs
    ADD COLUMN IF NOT EXISTS home_runs_allowed INTEGER;

COMMENT ON COLUMN mlb_players.pitcher_game_logs.home_runs_allowed IS
    'HR allowed by the pitcher in this game (MLB Stats API pitching gameLog homeRuns). FIP/xFIP input. Backfilled by mlb.scripts.loaders.backfill_pitcher_hr_allowed.';
