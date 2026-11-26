-- A lineup's strikeout rate beside the league average, for the latest season with games.
--
-- The rate is the team's batters' strikeouts over their plate appearances, summed over the
-- season from the batter game logs. Teams with fewer than 20 games (exhibition sides) are left
-- out of the league. NULLIF guards the division.
--
-- Parameters, in order:
--   1. team abbreviation
-- Run by: mlb/models/projection_model.py (_get_opponent_factor)
WITH team_rates AS (
    SELECT team,
           SUM(strikeouts)::numeric / NULLIF(SUM(plate_appearances), 0) AS k_rate
    FROM batter_game_logs
    WHERE EXTRACT(YEAR FROM game_date) = (
              SELECT EXTRACT(YEAR FROM MAX(game_date)) FROM batter_game_logs
          )
    GROUP BY team
    HAVING COUNT(DISTINCT game_id) >= 20
)
SELECT t.k_rate AS team_value,
       (SELECT AVG(k_rate) FROM team_rates) AS league_average
FROM team_rates t
WHERE t.team = %s;
