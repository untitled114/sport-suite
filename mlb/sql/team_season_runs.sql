-- Runs scored, runs allowed and wins per team for one season.
--
-- The games table holds one row per game with a home and an away side. UNION ALL turns it into
-- one row per team per game, so a single GROUP BY covers both sides.
--
-- Parameters, in order:
--   1. season year (home side)
--   2. the same season year (away side)
-- Run by: mlb/scripts/loaders/calculate_team_stats.py (calculate_team_season_stats)
WITH per_team AS (
    SELECT home_team AS team, home_score AS runs_for, away_score AS runs_against
    FROM games
    WHERE EXTRACT(YEAR FROM game_date) = %s
      AND home_score IS NOT NULL AND away_score IS NOT NULL
    UNION ALL
    SELECT away_team AS team, away_score AS runs_for, home_score AS runs_against
    FROM games
    WHERE EXTRACT(YEAR FROM game_date) = %s
      AND home_score IS NOT NULL AND away_score IS NOT NULL
)
SELECT
    team,
    COUNT(*) AS games,
    AVG(runs_for) AS avg_runs_scored,
    AVG(runs_against) AS avg_runs_allowed,
    COUNT(*) FILTER (WHERE runs_for > runs_against) AS wins
FROM per_team
WHERE team IS NOT NULL
GROUP BY team
ORDER BY team;
