-- Every game a player has played against one opponent before a date, newest first.
--
-- The head-to-head features (career and last-five averages, home and away splits, trends) are
-- computed from these rows in the caller. Column aliases give the box-score columns the names
-- the caller's calculations use. Names are compared through unaccent() on both sides, and only
-- games before the target date are read, so a feature never sees its own game.
--
-- Parameters, in order:
--   1. player full name
--   2. opponent team abbreviation
--   3. target game date (exclusive upper bound)
-- Run by: nba/features/extractors/h2h_features.py (extract)
SELECT
    gl.game_date,
    gl.points,
    gl.rebounds,
    gl.assists,
    gl.three_pointers_made,
    gl.minutes_played AS minutes,
    gl.fg_made AS field_goals_made,
    gl.fg_attempted AS field_goals_attempted,
    gl.three_pt_attempted AS three_pointers_attempted,
    gl.is_home
FROM player_game_logs gl
JOIN player_profile pp ON gl.player_id = pp.player_id
WHERE unaccent(pp.full_name) = unaccent(%s)
  AND gl.opponent_abbrev = %s
  AND gl.game_date < %s
  AND gl.minutes_played > 0
ORDER BY gl.game_date DESC;
