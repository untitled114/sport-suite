-- A team's runs allowed per game beside the league average, for the latest season.
--
-- The season table also holds one-game rows for exhibition sides, so "the league" is the teams
-- with at least 20 games; without that the average would be pulled by a handful of single games.
--
-- Parameters, in order:
--   1. team abbreviation
-- Run by: mlb/models/projection_model.py (_get_opponent_factor)
WITH league AS (
    SELECT team_abbrev, avg_runs_allowed
    FROM team_season_stats
    WHERE season = (SELECT MAX(season) FROM team_season_stats)
      AND games >= 20
      AND avg_runs_allowed IS NOT NULL
)
SELECT t.avg_runs_allowed AS team_value,
       (SELECT AVG(avg_runs_allowed) FROM league) AS league_average
FROM league t
WHERE t.team_abbrev = %s;
