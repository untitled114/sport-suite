-- A pitcher's last 30 game logs before a date, newest first.
--
-- The rolling averages, spread and trend are computed from these rows in the caller. Only
-- games before the target date are read, so a feature never sees its own game.
--
-- Parameters, in order:
--   1. pitcher full name
--   2. target game date (exclusive upper bound)
-- Run by: mlb/features/extractors/pitcher_features.py (extract)
SELECT
    pgl.game_date,
    pgl.innings_pitched,
    pgl.strikeouts,
    pgl.batters_faced,
    pgl.pitches,
    pgl.earned_runs,
    pgl.hits_allowed,
    pgl.walks
FROM pitcher_game_logs pgl
JOIN pitcher_profile pp ON pgl.player_id = pp.player_id
WHERE unaccent(pp.full_name) = unaccent(%s)
  AND pgl.game_date < %s
ORDER BY pgl.game_date DESC, pgl.game_id DESC
LIMIT 30;
