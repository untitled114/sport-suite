-- A player's last 10 games with minutes played before a date, newest first.
--
-- Minutes stability, recent plus/minus and a usage proxy are computed from these rows in the
-- caller. Both sides of the name comparison go through unaccent(), because profile names carry
-- diacritics and the caller's spelling may not.
--
-- Parameters, in order:
--   1. player full name
--   2. target game date (exclusive upper bound)
-- Run by: nba/features/extractors/game_context_features.py (_compute_player_features)
SELECT gl.minutes_played, gl.plus_minus, gl.points, gl.fg_attempted
FROM player_game_logs gl
JOIN player_profile pp ON gl.player_id = pp.player_id
WHERE unaccent(pp.full_name) = unaccent(%s)
  AND gl.game_date < %s
  AND gl.minutes_played > 0
ORDER BY gl.game_date DESC
LIMIT 10;
