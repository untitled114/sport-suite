-- Games played, wins and losses per team for one regular season, from final scores.
--
-- Each game is read twice, once per side (UNION ALL), with a boolean "won"; COUNT(*) FILTER
-- then gives wins and losses in one pass. A game counts once both scores are in.
--
-- Parameters, in order:
--   1. season (end year: 2027 is 2026-27), for the home side
--   2. the same season, for the away side
-- Run by: nba/scripts/loaders/calculate_team_stats.py (calculate_team_season_stats)
SELECT team,
       COUNT(*) AS games,
       COUNT(*) FILTER (WHERE won) AS wins,
       COUNT(*) FILTER (WHERE NOT won) AS losses
FROM (
    SELECT g.home_team AS team, g.home_score > g.away_score AS won FROM games g
     WHERE g.season = %s AND g.home_score IS NOT NULL AND g.away_score IS NOT NULL
       AND (g.season_type = 'Regular Season'
        OR NOT EXISTS (SELECT 1 FROM games t
                        WHERE t.season = g.season AND t.season_type IS NOT NULL))
    UNION ALL
    SELECT g.away_team, g.away_score > g.home_score FROM games g
     WHERE g.season = %s AND g.home_score IS NOT NULL AND g.away_score IS NOT NULL
       AND (g.season_type = 'Regular Season'
        OR NOT EXISTS (SELECT 1 FROM games t
                        WHERE t.season = g.season AND t.season_type IS NOT NULL))
) r
GROUP BY team;
