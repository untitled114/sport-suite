-- games.team_game_logs had no unique key, so the game loader's
-- upsert (ON CONFLICT team_abbrev, game_id, game_date) could not run. Additive; reversible
-- with: DROP INDEX games.uq_tgl_team_game;
-- Preconditions checked 2026-10-29: 11,682 rows, 0 duplicate (team_abbrev, game_id), 0 null keys.
CREATE UNIQUE INDEX IF NOT EXISTS uq_tgl_team_game
    ON games.team_game_logs (team_abbrev, game_id, game_date);
