-- Team-games whose player box scores do not add up to the team's points.
--
-- A reconciliation check between two independently loaded tables: the sum of the players'
-- points per (date, team) against the team game log. IS DISTINCT FROM also catches the case
-- where the players' side is missing entirely (NULL), which a plain <> would skip.
--
-- Parameters, in order:
--   1. start: first game date (named parameter)
--   2. end: last game date (named parameter)
-- Run by: scripts/ops/backfill_player_box_scores.py
WITH p AS (
    SELECT game_date, team_abbrev, sum(points) AS pts
      FROM players.player_game_logs
     WHERE game_date BETWEEN %(start)s AND %(end)s
     GROUP BY 1, 2)
SELECT t.game_date, t.team_abbrev
  FROM games.team_game_logs t
  LEFT JOIN p USING (game_date, team_abbrev)
 WHERE t.game_date BETWEEN %(start)s AND %(end)s
   AND p.pts IS DISTINCT FROM t.points
 ORDER BY 1, 2;
