-- Pace, true shooting and rebound share per team for one regular season.
--
-- Possessions per team-game are estimated from the box score (field-goal attempts + turnovers
-- + 0.44 x free-throw attempts; the feed has no offensive-rebound column). A self-join on the
-- game pairs each team with its opponent, and pace is the average of the two sides'
-- possessions per game.
-- True shooting is points / (2 x (FGA + 0.44 x FTA)) over the season's totals. Rebound share is
-- the team's rebounds over all rebounds in its games. NULLIF guards both divisions.
-- A season counts as "regular" by its label; a season loaded before the label existed (no
-- game of that season carries one) is taken whole.
--
-- Parameters, in order:
--   1. season (end year: 2027 is 2026-27)
-- Run by: nba/scripts/loaders/calculate_team_stats.py (calculate_team_season_stats)
WITH game_poss AS (
    SELECT
        tgl.team_abbrev,
        tgl.game_id,
        tgl.fg_attempted + tgl.turnovers + 0.44 * tgl.ft_attempted AS team_poss,
        opp.fg_attempted + opp.turnovers + 0.44 * opp.ft_attempted AS opp_poss,
        tgl.points, tgl.fg_attempted, tgl.ft_attempted,
        tgl.rebounds, opp.rebounds AS opp_rebounds
    FROM team_game_logs tgl
    JOIN team_game_logs opp
        ON tgl.game_id = opp.game_id
        AND tgl.team_abbrev = opp.opponent
    JOIN games g ON g.game_id = tgl.game_id
    WHERE tgl.season = %s
      AND tgl.fg_attempted IS NOT NULL
      AND opp.fg_attempted IS NOT NULL
      AND (g.season_type = 'Regular Season'
           OR NOT EXISTS (SELECT 1 FROM games t
                           WHERE t.season = g.season AND t.season_type IS NOT NULL))
)
SELECT
    team_abbrev,
    COUNT(*) AS games,
    AVG((team_poss + opp_poss) / 2.0) AS avg_pace,
    SUM(points) / NULLIF(2.0 * (SUM(fg_attempted) + 0.44 * SUM(ft_attempted)), 0) AS true_shooting,
    SUM(rebounds)::numeric / NULLIF(SUM(rebounds) + SUM(opp_rebounds), 0) AS rebound_share
FROM game_poss
GROUP BY team_abbrev
ORDER BY team_abbrev;
