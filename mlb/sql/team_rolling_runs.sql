-- Average runs scored and allowed over each team's last N games.
--
-- One row per team per game (UNION ALL of the home and away sides), numbered newest first per
-- team with ROW_NUMBER() (the game id breaks the tie on a doubleheader day); the outer query
-- keeps the newest N and averages them.
--
-- Parameters, in order:
--   1. seasons (integer array) for the home side
--   2. the same seasons for the away side
--   3. N, the number of most recent games
-- Run by: mlb/scripts/loaders/calculate_team_stats.py (calculate_team_rolling_stats)
WITH per_team AS (
    SELECT home_team AS team, game_date, game_id,
           home_score AS runs_for, away_score AS runs_against
    FROM games
    WHERE EXTRACT(YEAR FROM game_date) = ANY(%s)
      AND home_score IS NOT NULL AND away_score IS NOT NULL
    UNION ALL
    SELECT away_team AS team, game_date, game_id,
           away_score AS runs_for, home_score AS runs_against
    FROM games
    WHERE EXTRACT(YEAR FROM game_date) = ANY(%s)
      AND home_score IS NOT NULL AND away_score IS NOT NULL
),
ranked AS (
    SELECT team, game_date, runs_for, runs_against,
           ROW_NUMBER() OVER (
               PARTITION BY team ORDER BY game_date DESC, game_id DESC
           ) AS rn
    FROM per_team
    WHERE team IS NOT NULL
)
SELECT team,
       COUNT(*) AS games,
       AVG(runs_for) AS avg_runs_scored,
       AVG(runs_against) AS avg_runs_allowed,
       MAX(game_date) AS as_of
FROM ranked
WHERE rn <= %s
GROUP BY team
ORDER BY team;
