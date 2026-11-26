-- ============================================================================
-- MLB database schema (DDL only)
-- ============================================================================
-- Target: the same TimescaleDB instance as the NBA schemas (database `sportsuite`).
-- All MLB data lives under `mlb_*` schemas, parallel to the NBA ones. This file is DDL
-- only; to apply it to an existing database by hand:
--     psql -h localhost -p 5500 -d sportsuite -f mlb/db/schema.sql
--
-- Conventions:
--   * All timestamps are TIMESTAMPTZ representing EST instants.
--   * `*_profile.full_name` may contain diacritics: always use unaccent() in
--     joins (the extension is created below).
--   * Baseball has TWO player roles: pitchers and batters. They are kept in
--     SEPARATE profile and game-log tables (see mlb_players).
--   * Projected stats (uppercase): STRIKEOUTS, HITS, TOTAL_BASES.
-- ============================================================================

CREATE EXTENSION IF NOT EXISTS unaccent;

CREATE SCHEMA IF NOT EXISTS mlb_players;
CREATE SCHEMA IF NOT EXISTS mlb_games;
CREATE SCHEMA IF NOT EXISTS mlb_teams;
CREATE SCHEMA IF NOT EXISTS mlb_features;


-- ============================================================================
-- SCHEMA: mlb_players  (player identity + game logs)
-- ============================================================================

-- Pitcher identity. full_name may contain diacritics (use unaccent() in joins).
CREATE TABLE IF NOT EXISTS mlb_players.pitcher_profile (
    player_id       BIGINT PRIMARY KEY,
    full_name       TEXT NOT NULL,
    team            TEXT,
    throws          TEXT,            -- 'L' / 'R'
    position        TEXT,            -- e.g. 'SP', 'RP'
    active          BOOLEAN DEFAULT TRUE,
    debut_date      DATE,
    created_at      TIMESTAMPTZ DEFAULT now(),
    updated_at      TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_pitcher_profile_name
    ON mlb_players.pitcher_profile (full_name);
CREATE INDEX IF NOT EXISTS idx_pitcher_profile_team
    ON mlb_players.pitcher_profile (team);

-- Batter identity. full_name may contain diacritics (use unaccent() in joins).
CREATE TABLE IF NOT EXISTS mlb_players.batter_profile (
    player_id       BIGINT PRIMARY KEY,
    full_name       TEXT NOT NULL,
    team            TEXT,
    bats            TEXT,            -- 'L' / 'R' / 'S'
    position        TEXT,            -- e.g. '1B', 'CF', 'DH'
    active          BOOLEAN DEFAULT TRUE,
    debut_date      DATE,
    created_at      TIMESTAMPTZ DEFAULT now(),
    updated_at      TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_batter_profile_name
    ON mlb_players.batter_profile (full_name);
CREATE INDEX IF NOT EXISTS idx_batter_profile_team
    ON mlb_players.batter_profile (team);

-- Per-game pitcher stat lines (feeds the STRIKEOUTS stat_type).
CREATE TABLE IF NOT EXISTS mlb_players.pitcher_game_logs (
    id              BIGSERIAL PRIMARY KEY,
    player_id       BIGINT NOT NULL,
    full_name       TEXT,
    team            TEXT,
    game_id         BIGINT NOT NULL,
    game_date       DATE NOT NULL,
    opponent        TEXT,
    home_away       TEXT,            -- 'home' / 'away'
    innings_pitched NUMERIC(4, 1),   -- e.g. 6.1 (6 IP + 1 out)
    strikeouts      INTEGER,
    batters_faced   INTEGER,
    pitches         INTEGER,
    earned_runs     INTEGER,
    hits_allowed    INTEGER,
    walks           INTEGER,
    created_at      TIMESTAMPTZ DEFAULT now(),
    UNIQUE (player_id, game_id)
);
CREATE INDEX IF NOT EXISTS idx_pitcher_logs_player
    ON mlb_players.pitcher_game_logs (player_id);
CREATE INDEX IF NOT EXISTS idx_pitcher_logs_date
    ON mlb_players.pitcher_game_logs (game_date);
CREATE INDEX IF NOT EXISTS idx_pitcher_logs_player_date
    ON mlb_players.pitcher_game_logs (player_id, game_date DESC);

-- Per-game batter stat lines (feeds HITS / TOTAL_BASES stat_types).
CREATE TABLE IF NOT EXISTS mlb_players.batter_game_logs (
    id               BIGSERIAL PRIMARY KEY,
    player_id        BIGINT NOT NULL,
    full_name        TEXT,
    team             TEXT,
    game_id          BIGINT NOT NULL,
    game_date        DATE NOT NULL,
    opponent         TEXT,
    home_away        TEXT,           -- 'home' / 'away'
    at_bats          INTEGER,
    hits             INTEGER,
    total_bases      INTEGER,
    runs             INTEGER,
    rbis             INTEGER,
    home_runs        INTEGER,
    walks            INTEGER,
    strikeouts       INTEGER,
    plate_appearances INTEGER,
    created_at       TIMESTAMPTZ DEFAULT now(),
    UNIQUE (player_id, game_id)
);
CREATE INDEX IF NOT EXISTS idx_batter_logs_player
    ON mlb_players.batter_game_logs (player_id);
CREATE INDEX IF NOT EXISTS idx_batter_logs_date
    ON mlb_players.batter_game_logs (game_date);
CREATE INDEX IF NOT EXISTS idx_batter_logs_player_date
    ON mlb_players.batter_game_logs (player_id, game_date DESC);


-- ============================================================================
-- SCHEMA: mlb_games  (schedule + results)
-- ============================================================================

CREATE TABLE IF NOT EXISTS mlb_games.games (
    game_id     BIGINT PRIMARY KEY,
    game_date   DATE NOT NULL,
    home_team   TEXT NOT NULL,
    away_team   TEXT NOT NULL,
    home_score  INTEGER,
    away_score  INTEGER,
    status      TEXT,                 -- 'scheduled' / 'in_progress' / 'final'
    venue       TEXT,
    park_factor NUMERIC(6, 3),        -- run-scoring park factor on a 100 scale (100 = neutral)
    game_time   TIMESTAMPTZ,          -- first-pitch (EST)
    created_at  TIMESTAMPTZ DEFAULT now(),
    updated_at  TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_games_date ON mlb_games.games (game_date);
CREATE INDEX IF NOT EXISTS idx_games_home ON mlb_games.games (home_team);
CREATE INDEX IF NOT EXISTS idx_games_away ON mlb_games.games (away_team);

-- Per-team, per-game team-level stat lines. Reserved: nothing writes this table yet.
CREATE TABLE IF NOT EXISTS mlb_games.team_game_logs (
    id          BIGSERIAL PRIMARY KEY,
    game_id     BIGINT NOT NULL,
    game_date   DATE NOT NULL,
    team        TEXT NOT NULL,
    opponent    TEXT,
    home_away   TEXT,
    runs        INTEGER,
    hits        INTEGER,
    runs_allowed INTEGER,
    strikeouts_batting INTEGER,       -- team batters' Ks
    strikeouts_pitching INTEGER,      -- team pitchers' Ks
    win         BOOLEAN,
    created_at  TIMESTAMPTZ DEFAULT now(),
    UNIQUE (game_id, team)
);
CREATE INDEX IF NOT EXISTS idx_team_game_logs_team
    ON mlb_games.team_game_logs (team);
CREATE INDEX IF NOT EXISTS idx_team_game_logs_date
    ON mlb_games.team_game_logs (game_date);

-- Probable starting pitchers per game (hydrated from the schedule endpoint).
CREATE TABLE IF NOT EXISTS mlb_games.probable_pitchers (
    id           BIGSERIAL PRIMARY KEY,
    game_id      BIGINT NOT NULL,
    game_date    DATE NOT NULL,
    team         TEXT NOT NULL,
    pitcher_id   BIGINT,
    pitcher_name TEXT,
    created_at   TIMESTAMPTZ DEFAULT now(),
    UNIQUE (game_id, team)
);
CREATE INDEX IF NOT EXISTS idx_probable_pitchers_date
    ON mlb_games.probable_pitchers (game_date);
CREATE INDEX IF NOT EXISTS idx_probable_pitchers_pitcher
    ON mlb_games.probable_pitchers (pitcher_id);


-- ============================================================================
-- SCHEMA: mlb_teams  (team reference + stats)
-- ============================================================================

CREATE TABLE IF NOT EXISTS mlb_teams.teams (
    team_id     BIGINT PRIMARY KEY,
    team_abbrev TEXT UNIQUE NOT NULL,  -- as the stats API gives it (CWS, KC, SD, SF, TB, WSH ...)
    team_name   TEXT NOT NULL,
    league      TEXT,                  -- 'AL' / 'NL'
    division    TEXT,                  -- 'East' / 'Central' / 'West'
    venue       TEXT,
    created_at  TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_teams_abbrev ON mlb_teams.teams (team_abbrev);

-- Per-season team aggregates, written by calculate_team_stats.py. The projection reads
-- avg_runs_allowed from here for its opponent factor.
CREATE TABLE IF NOT EXISTS mlb_teams.team_season_stats (
    id          BIGSERIAL PRIMARY KEY,
    team_abbrev TEXT NOT NULL,
    season      INTEGER NOT NULL,
    games       INTEGER,
    avg_runs_scored      NUMERIC(6, 3),
    avg_runs_allowed     NUMERIC(6, 3),
    run_differential     NUMERIC(7, 3),
    win_pct              NUMERIC(5, 3),
    -- Reserved: nothing writes these two yet. The lineup strikeout rate the projection uses
    -- is computed from the batter game logs.
    batting_k_rate       NUMERIC(5, 3),   -- lineup strikeout rate
    pitching_runs_allowed NUMERIC(6, 3),  -- runs allowed (pitching)
    created_at  TIMESTAMPTZ DEFAULT now(),
    UNIQUE (team_abbrev, season)
);
CREATE INDEX IF NOT EXISTS idx_team_season_stats_team
    ON mlb_teams.team_season_stats (team_abbrev, season);

-- Last-N-game rolling team form. Columns match calculate_team_stats.py (writer).
CREATE TABLE IF NOT EXISTS mlb_teams.team_rolling_stats (
    id          BIGSERIAL PRIMARY KEY,
    team_abbrev TEXT NOT NULL,
    window_size INTEGER NOT NULL,     -- e.g. 10 for L10
    games       INTEGER,
    avg_runs_scored      NUMERIC(6, 3),
    avg_runs_allowed     NUMERIC(6, 3),
    as_of_date  DATE NOT NULL,
    created_at  TIMESTAMPTZ DEFAULT now(),
    UNIQUE (team_abbrev, window_size)
);
CREATE INDEX IF NOT EXISTS idx_team_rolling_stats_team
    ON mlb_teams.team_rolling_stats (team_abbrev, as_of_date DESC);

-- Team batting performance. Reserved: nothing writes this table yet.
CREATE TABLE IF NOT EXISTS mlb_teams.team_batting_performance (
    id            BIGSERIAL PRIMARY KEY,
    team          TEXT NOT NULL,
    as_of_date    DATE NOT NULL,
    vs_handedness TEXT,               -- 'L' / 'R' / 'ALL'
    strikeout_rate NUMERIC(5, 3),     -- how often this team strikes out
    hits_per_game  NUMERIC(6, 3),
    total_bases_per_game NUMERIC(6, 3),
    created_at    TIMESTAMPTZ DEFAULT now(),
    UNIQUE (team, as_of_date, vs_handedness)
);
CREATE INDEX IF NOT EXISTS idx_team_batting_perf_team
    ON mlb_teams.team_batting_performance (team, as_of_date DESC);


-- ============================================================================
-- SCHEMA: mlb_features  (feature store)
-- ============================================================================

-- Named, versioned feature set definitions, read by mlb.features.feature_store.FeatureStore.
-- Reserved: nothing registers a set yet.
CREATE TABLE IF NOT EXISTS mlb_features.feature_sets (
    id            BIGSERIAL PRIMARY KEY,
    name          TEXT NOT NULL,
    version       TEXT NOT NULL,
    stat_type        TEXT,
    feature_count INTEGER,
    feature_names JSONB,              -- ordered list of column names
    description   TEXT,
    is_active     BOOLEAN DEFAULT TRUE,
    created_at    TIMESTAMPTZ DEFAULT now(),
    UNIQUE (name, version)
);
CREATE INDEX IF NOT EXISTS idx_feature_sets_stat_type
    ON mlb_features.feature_sets (stat_type);

-- Computed feature vectors per player, game and stat type. Column names match
-- mlb.features.feature_store.FeatureStore SQL.
CREATE TABLE IF NOT EXISTS mlb_features.computed_features (
    id               BIGSERIAL PRIMARY KEY,
    player_name      TEXT NOT NULL,
    player_id        BIGINT,
    game_id          BIGINT,
    game_date        DATE NOT NULL,
    stat_type        TEXT NOT NULL,    -- which stat the vector is for (STRIKEOUTS / HITS / ...)
    feature_set_name TEXT NOT NULL,
    feature_values   JSONB NOT NULL,   -- {feature_name: value}
    computed_at      TIMESTAMPTZ DEFAULT now(),
    created_at       TIMESTAMPTZ DEFAULT now(),
    UNIQUE (player_name, game_date, stat_type, feature_set_name)
);
CREATE INDEX IF NOT EXISTS idx_computed_features_player
    ON mlb_features.computed_features (player_id, stat_type);
CREATE INDEX IF NOT EXISTS idx_computed_features_name
    ON mlb_features.computed_features (player_name, stat_type);
CREATE INDEX IF NOT EXISTS idx_computed_features_date
    ON mlb_features.computed_features (game_date);

-- Feature-level metadata (drift baselines, importance). Column names match
-- mlb.features.feature_store.FeatureStore.update_feature_metadata SQL.
CREATE TABLE IF NOT EXISTS mlb_features.feature_metadata (
    id               BIGSERIAL PRIMARY KEY,
    feature_set_name TEXT NOT NULL,
    feature_name     TEXT NOT NULL,
    train_mean       NUMERIC,
    train_std        NUMERIC,
    train_min        NUMERIC,
    train_max        NUMERIC,
    shap_importance  NUMERIC,
    importance_rank  INTEGER,
    updated_at       TIMESTAMPTZ DEFAULT now(),
    UNIQUE (feature_set_name, feature_name)
);
CREATE INDEX IF NOT EXISTS idx_feature_metadata_set
    ON mlb_features.feature_metadata (feature_set_name);

-- ==========================================================================
-- STATCAST (Baseball Savant via pybaseball)
-- ==========================================================================
-- Per-game advanced skill metrics aggregated from raw pitch-by-pitch Statcast
-- data. Statcast `pitcher`/`batter` ids EQUAL our statsapi `player_id`, and
-- `game_pk` EQUALS our `game_id`, so these tables join directly to
-- mlb_players.pitcher_game_logs / batter_game_logs with no name matching.
-- Loaded and upserted by mlb.scripts.loaders.load_statcast.

-- Pitcher per-game Statcast metrics (feeds STRIKEOUTS).
--   swstr_pct   = swinging strikes / pitches
--   csw_pct     = (called + swinging strikes) / pitches
--   whiff_pct   = whiffs / swings
--   chase_pct   = swings on out-of-zone pitches / out-of-zone pitches
--   putaway_pct = strikeouts / two-strike pitches
CREATE TABLE IF NOT EXISTS mlb_players.statcast_pitcher_games (
    player_id         BIGINT,
    game_id           BIGINT,
    game_date         DATE,
    pitches           INT,
    swinging_strikes  INT,
    called_strikes    INT,
    swstr_pct         NUMERIC,
    csw_pct           NUMERIC,
    whiff_pct         NUMERIC,
    chase_pct         NUMERIC,
    avg_fastball_velo NUMERIC,
    putaway_pct       NUMERIC,
    strikeouts        INT,
    batters_faced     INT,
    created_at        TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (player_id, game_id)
);
CREATE INDEX IF NOT EXISTS idx_statcast_pitcher_player_date
    ON mlb_players.statcast_pitcher_games (player_id, game_date DESC);

-- Batter per-game Statcast metrics (feeds HITS / TOTAL_BASES).
--   batted_balls   = balls in play (type == 'X')
--   avg_exit_velo  = mean launch_speed over batted balls
--   hard_hit_pct   = share of batted balls with launch_speed >= 95
--   barrel_pct     = share of batted balls with launch_speed_angle == 6
--   sweet_spot_pct = share of batted balls with launch_angle in [8, 32]
--   xba/xwoba/xslg = mean Savant estimated_*_using_speedangle
--   whiff_pct/chase_pct = batter swing-and-miss / out-of-zone chase rates
CREATE TABLE IF NOT EXISTS mlb_players.statcast_batter_games (
    player_id        BIGINT,
    game_id          BIGINT,
    game_date        DATE,
    batted_balls     INT,
    avg_exit_velo    NUMERIC,
    hard_hit_pct     NUMERIC,
    barrel_pct       NUMERIC,
    sweet_spot_pct   NUMERIC,
    avg_launch_angle NUMERIC,
    xba              NUMERIC,
    xwoba            NUMERIC,
    xslg             NUMERIC,
    whiff_pct        NUMERIC,
    chase_pct        NUMERIC,
    pa               INT,
    created_at       TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (player_id, game_id)
);
CREATE INDEX IF NOT EXISTS idx_statcast_batter_player_date
    ON mlb_players.statcast_batter_games (player_id, game_date DESC);
