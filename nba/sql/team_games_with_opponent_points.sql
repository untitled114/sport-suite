-- Every finished team-game with the points the team scored and allowed.
--
-- The team game log has one row per team per game. A self-join on the game pairs each row
-- with its opponent's row, so points allowed come from the same table. Ordered per team by
-- date, ready for a rolling window in the caller.
--
-- Parameters: none.
-- Run by: nba/scripts/loaders/compute_team_rolling_stats.py (get_team_game_history)
SELECT
    a.team_abbrev,
    a.game_date,
    a.game_id,
    a.points AS team_pts,
    b.points AS opp_pts,
    a.is_home
FROM team_game_logs a
JOIN team_game_logs b
    ON a.game_id = b.game_id
    AND a.team_abbrev = b.opponent
WHERE a.points IS NOT NULL
  AND b.points IS NOT NULL
  AND a.team_abbrev != a.opponent
ORDER BY a.team_abbrev, a.game_date, a.game_id;
