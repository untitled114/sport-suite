-- The team a player appeared for in each of his last 30 games before a date, newest first.
--
-- The caller reads these to tell whether he changed teams recently and how many games he has
-- played for the current one. Names are compared through unaccent() on both sides.
--
-- Parameters, in order:
--   1. player full name
--   2. target game date (exclusive upper bound)
-- Run by: nba/features/extractors/temporal_features.py (_compute_team_continuity)
SELECT gl.team_abbrev, gl.game_date
FROM player_game_logs gl
JOIN player_profile pp ON gl.player_id = pp.player_id
WHERE unaccent(pp.full_name) = unaccent(%s)
  AND gl.game_date < %s
  AND gl.minutes_played > 0
ORDER BY gl.game_date DESC
LIMIT 30;
