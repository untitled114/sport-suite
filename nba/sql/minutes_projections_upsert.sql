-- Store the projected minutes per player (insert, or replace on re-run).
--
-- Written with execute_values: the single VALUES placeholder expands to one tuple per player.
--
-- Parameters, in order:
--   1. the rows (player_id, projected, previous-season, current-season minutes, method)
-- Run by: nba/scripts/loaders/calculate_minutes_projections.py (save_to_database)
INSERT INTO player_minutes_projections
(player_id, projected_mpg, prev_season_mpg, curr_season_mpg, method)
VALUES %s
ON CONFLICT (player_id)
DO UPDATE SET
    projected_mpg = EXCLUDED.projected_mpg,
    prev_season_mpg = EXCLUDED.prev_season_mpg,
    curr_season_mpg = EXCLUDED.curr_season_mpg,
    method = EXCLUDED.method,
    updated_at = NOW();
