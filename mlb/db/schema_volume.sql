-- ============================================================================
-- MLB VOLUME / OPPORTUNITY SCHEMA EXTENSIONS
-- ============================================================================
-- Additive DDL for the VOLUME / OPPORTUNITY feature suite. Kept separate from the base MLB schema.
--
-- Stores per-game batting-order (lineup slot) for every batter who appeared,
-- backfilled from the MLB Stats API boxscore ``battingOrder`` field.
-- Lineup slot drives expected plate appearances:
-- a #2 hitter gets materially more PA over a season than a #8 hitter.
-- ============================================================================

CREATE TABLE IF NOT EXISTS mlb_players.batter_lineup_slots (
    id            BIGSERIAL PRIMARY KEY,
    player_id     BIGINT NOT NULL,
    game_id       BIGINT NOT NULL,
    game_date     DATE NOT NULL,
    -- 1..9 = lineup slot the batter occupied (starters). NULL/0 = pinch/sub
    -- with no opening lineup slot recorded.
    batting_order INTEGER,
    created_at    TIMESTAMPTZ DEFAULT now(),
    UNIQUE (player_id, game_id)
);

CREATE INDEX IF NOT EXISTS idx_lineup_slots_player
    ON mlb_players.batter_lineup_slots (player_id);
CREATE INDEX IF NOT EXISTS idx_lineup_slots_date
    ON mlb_players.batter_lineup_slots (game_date);
CREATE INDEX IF NOT EXISTS idx_lineup_slots_player_date
    ON mlb_players.batter_lineup_slots (player_id, game_date DESC);

COMMENT ON TABLE mlb_players.batter_lineup_slots IS
    'Per-game batting-order slot (1-9) for each batter, from MLB Stats API boxscore battingOrder (100/200/... = slot 1/2/...). Backfilled by mlb.scripts.loaders.backfill_lineup_slots.';
