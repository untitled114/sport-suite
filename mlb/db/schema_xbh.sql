-- ============================================================================
-- MLB EXTRA-BASE-HIT + wOBA-COMPONENT SCHEMA EXTENSIONS
-- ============================================================================
-- Additive DDL for wOBA's components. Kept separate from the base MLB schema.
--
-- wOBA applies linear weights to each way of reaching base:
--   wOBA = (wBB*uBB + wHBP*HBP + w1B*1B + w2B*2B + w3B*3B + wHR*HR)
--          / (AB + BB - IBB + SF + HBP)
-- The base batter_game_logs table tracks AB / H / TB / HR / BB / K / PA but not
-- doubles, triples, HBP, intentional walks, or sac flies. Without doubles/triples
-- we cannot split hits into 1B/2B/3B; without IBB/SF/HBP we cannot build the true
-- denominator. All five are present in the MLB Stats API hitting gameLog ``stat``
-- payload (the source load_batter_gamelogs.py reads); these columns store them.
--
-- Backfilled by mlb.scripts.loaders.backfill_batter_xbh (UPDATE-only, matched by
-- (player_id, game_id); never defaults a missing value).
-- ============================================================================

ALTER TABLE mlb_players.batter_game_logs
    ADD COLUMN IF NOT EXISTS doubles INTEGER,
    ADD COLUMN IF NOT EXISTS triples INTEGER,
    ADD COLUMN IF NOT EXISTS hit_by_pitch INTEGER,
    ADD COLUMN IF NOT EXISTS intentional_walks INTEGER,
    ADD COLUMN IF NOT EXISTS sac_flies INTEGER;

COMMENT ON COLUMN mlb_players.batter_game_logs.doubles IS
    'Doubles hit in this game (MLB Stats API hitting gameLog doubles). 1B = H - 2B - 3B - HR. True-wOBA input. Backfilled by mlb.scripts.loaders.backfill_batter_xbh.';
COMMENT ON COLUMN mlb_players.batter_game_logs.triples IS
    'Triples hit in this game (MLB Stats API hitting gameLog triples). True-wOBA input.';
COMMENT ON COLUMN mlb_players.batter_game_logs.hit_by_pitch IS
    'Hit-by-pitch in this game (MLB Stats API hitting gameLog hitByPitch). True-wOBA numerator + denominator term.';
COMMENT ON COLUMN mlb_players.batter_game_logs.intentional_walks IS
    'Intentional walks in this game (MLB Stats API hitting gameLog intentionalWalks). Removed from wOBA denominator (uBB = BB - IBB).';
COMMENT ON COLUMN mlb_players.batter_game_logs.sac_flies IS
    'Sacrifice flies in this game (MLB Stats API hitting gameLog sacFlies). True-wOBA denominator term.';
