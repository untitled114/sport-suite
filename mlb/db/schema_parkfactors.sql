-- ============================================================================
-- MLB PARK FACTORS SCHEMA EXTENSION
-- ============================================================================
-- Additive DDL for the park factors. Kept separate from the base MLB schema.
--
-- This table holds factors computed from the stored games by
-- mlb.scripts.loaders.compute_park_factors:
--   runs_factor  = (park runs/game, home+away combined at the venue)
--                  / (league runs/game)
--   hr_factor    = (park HR/game at the venue)   / (league HR/game)
--   hits_factor  = (park hits/game at the venue) / (league hits/game)
-- All three are aggregated over a multi-season rolling window (2024-present) from
-- mlb_games.games (scores) and mlb_players.batter_game_logs (HR, hits) joined to
-- games by game_id. 1.0 = league-neutral. n_games records the sample size so the
-- reader can fall back to neutral (and flag *_known = 0) when too thin.
-- ============================================================================

CREATE TABLE IF NOT EXISTS mlb_teams.park_factors (
    home_team    TEXT NOT NULL,           -- canonical 3-letter home-team abbrev
    window_label TEXT NOT NULL,           -- e.g. 'rolling_2024_2026'
    runs_factor  NUMERIC,                 -- 1.0 = league-neutral
    hr_factor    NUMERIC,
    hits_factor  NUMERIC,
    n_games      INTEGER NOT NULL DEFAULT 0,
    computed_at  TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (home_team, window_label)
);

COMMENT ON TABLE mlb_teams.park_factors IS
    'Data-derived MLB park factors (runs/HR/hits relative to league avg), computed from mlb_games.games + mlb_players.batter_game_logs over a rolling multi-season window by mlb.scripts.loaders.compute_park_factors. 1.0 = neutral.';
