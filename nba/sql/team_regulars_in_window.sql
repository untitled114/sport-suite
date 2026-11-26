-- A team's regulars: players who appeared in at least N of its games in a date window.
--
-- Only games with minutes played count as an appearance; HAVING keeps the players at or
-- above the threshold.
--
-- Parameters, in order:
--   1. team abbreviation
--   2. window start date (inclusive)
--   3. window end date (exclusive)
--   4. N, the minimum number of games played
-- Run by: nba/scripts/loaders/backfill_injuries_from_games.py (get_active_players_for_team)
SELECT pgl.player_id, pp.full_name, COUNT(*) as games_played
FROM player_game_logs pgl
JOIN player_profile pp ON pgl.player_id = pp.player_id
WHERE pgl.team_abbrev = %s
  AND pgl.game_date >= %s
  AND pgl.game_date < %s
  AND pgl.minutes_played > 0
GROUP BY pgl.player_id, pp.full_name
HAVING COUNT(*) >= %s;
