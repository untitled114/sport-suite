-- A batter's last 30 game logs before a date, newest first.
--
-- The rolling averages, spread and trend are computed from these rows in the caller. Only
-- games before the target date are read, so a feature never sees its own game.
--
-- Parameters, in order:
--   1. batter full name
--   2. target game date (exclusive upper bound)
-- Run by: mlb/features/extractors/batter_features.py (extract)
SELECT
    bgl.game_date,
    bgl.at_bats,
    bgl.hits,
    bgl.total_bases,
    bgl.runs,
    bgl.rbis,
    bgl.home_runs,
    bgl.walks,
    bgl.strikeouts,
    bgl.plate_appearances
FROM batter_game_logs bgl
JOIN batter_profile bp ON bgl.player_id = bp.player_id
WHERE unaccent(bp.full_name) = unaccent(%s)
  AND bgl.game_date < %s
ORDER BY bgl.game_date DESC, bgl.game_id DESC
LIMIT 30;
