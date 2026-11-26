-- players.player_season_stats moves to the END-year season label every other table uses
-- (games, team_season_stats, team_game_logs: "2026-27" = 2027). load_nba_players.py wrote START
-- years (2026 = 2026-27) until 2026-11-10. Two steps, so (player_id, season) never collides.
BEGIN;
UPDATE players.player_season_stats SET season = season + 10001 WHERE season < 10000;
UPDATE players.player_season_stats SET season = season - 10000 WHERE season > 10000;
COMMIT;
