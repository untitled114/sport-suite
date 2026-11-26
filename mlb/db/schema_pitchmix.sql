-- ===========================================================================
-- schema_pitchmix.sql - Pitch-Mix / Batted-Ball / Real BvP feature tables
-- ===========================================================================
-- New tables for the raw-pitch feature suite (pitch-mix, batted-ball profile,
-- true batter-vs-pitcher) which the aggregate-only tables (statcast_pitcher_games / statcast_batter_games)
-- cannot express.
--
-- All tables live under the mlb_players schema. CREATE TABLE IF NOT EXISTS
-- only - this file is safe to apply repeatedly and never edits schema.sql.
--
-- Every row is aggregated from Baseball Savant pitch-by-pitch data (pybaseball).
-- Dates are Eastern at the loader boundary.
-- ===========================================================================

-- ---------------------------------------------------------------------------
-- Pitcher pitch-mix per game (feeds STRIKEOUTS).
--   num_pitch_types       = distinct non-null pitch_type thrown
--   fastball_velo         = mean release_speed over fastballs {FF,SI,FC}
--   fastball_spin         = mean release_spin_rate over fastballs
--   breaking_spin         = mean release_spin_rate over breaking balls {SL,CU,KC,ST,SV,CS}
--   zone_rate             = share of pitches in the strike zone (zone 1-9)
--   first_strike_rate     = share of PAs whose first pitch (balls=0,strikes=0) was a strike
--   *_whiff_rate          = whiffs / swings for that pitch group
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS mlb_players.statcast_pitcher_pitchmix_games (
    player_id           BIGINT,
    game_id             BIGINT,
    game_date           DATE,
    num_pitch_types     INT,
    fastball_velo       NUMERIC,
    fastball_spin       NUMERIC,
    breaking_spin       NUMERIC,
    zone_rate           NUMERIC,
    first_strike_rate   NUMERIC,
    fastball_whiff_rate NUMERIC,
    slider_whiff_rate   NUMERIC,
    curve_whiff_rate    NUMERIC,
    changeup_whiff_rate NUMERIC,
    created_at          TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (player_id, game_id)
);
CREATE INDEX IF NOT EXISTS idx_statcast_pitchmix_player_date
    ON mlb_players.statcast_pitcher_pitchmix_games (player_id, game_date DESC);

-- ---------------------------------------------------------------------------
-- Batter batted-ball profile per game (feeds HITS / TOTAL_BASES).
--   bbe         = batted-ball events (balls in play with a bb_type)
--   gb/fb/ld/popup_rate = share of BBE by bb_type
--                         (ground_ball / fly_ball / line_drive / popup)
--   pull_rate   = share of BBE hit to the pull side (Statcast hc_x/hc_y geometry,
--                 handedness-adjusted)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS mlb_players.statcast_batter_battedball_games (
    player_id   BIGINT,
    game_id     BIGINT,
    game_date   DATE,
    bbe         INT,
    gb_rate     NUMERIC,
    fb_rate     NUMERIC,
    ld_rate     NUMERIC,
    popup_rate  NUMERIC,
    pull_rate   NUMERIC,
    created_at  TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (player_id, game_id)
);
CREATE INDEX IF NOT EXISTS idx_statcast_battedball_player_date
    ON mlb_players.statcast_batter_battedball_games (player_id, game_date DESC);

-- ---------------------------------------------------------------------------
-- Per-encounter batter-vs-pitcher rows (LEAKAGE-SAFE GRANULARITY).
-- One row per (batter, pitcher, game) summarizing the PAs in that single game.
-- The H2H extractor sums rows with game_date STRICTLY BEFORE the target date so
-- BvP is reconstructed "as-of" with no same-game leakage.
--   pa    = distinct plate appearances (terminal pitches with an `events`)
--   hits  = single+double+triple+home_run
--   k     = strikeout (+ strikeout_double_play)
--   bb    = walk (+ intent_walk)
--   tb    = total bases (1B*1 + 2B*2 + 3B*3 + HR*4)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS mlb_players.bvp_encounters (
    batter_id   BIGINT,
    pitcher_id  BIGINT,
    game_id     BIGINT,
    game_date   DATE,
    pa          INT,
    hits        INT,
    k           INT,
    bb          INT,
    tb          INT,
    created_at  TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (batter_id, pitcher_id, game_id)
);
CREATE INDEX IF NOT EXISTS idx_bvp_encounters_pair_date
    ON mlb_players.bvp_encounters (batter_id, pitcher_id, game_date);

-- ---------------------------------------------------------------------------
-- Optional cumulative (batter, pitcher) rollup of bvp_encounters.
-- Convenience aggregate; the extractor prefers bvp_encounters for as-of safety.
-- last_game_date lets a consumer filter strictly-before a target date if it ever
-- uses this rollup instead of the per-encounter rows.
--   ops_proxy = (hits + bb)/PA  +  tb/PA      (OBP-proxy + SLG-proxy)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS mlb_players.matchup_history (
    batter_id      BIGINT,
    pitcher_id     BIGINT,
    last_game_date DATE,
    pa             INT,
    hits           INT,
    k              INT,
    bb             INT,
    tb             INT,
    avg            NUMERIC,
    ops_proxy      NUMERIC,
    k_rate         NUMERIC,
    created_at     TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (batter_id, pitcher_id)
);
CREATE INDEX IF NOT EXISTS idx_matchup_history_pitcher
    ON mlb_players.matchup_history (pitcher_id);
