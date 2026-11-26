-- A team's average pace and score margin over its last 10 games before a date.
--
-- Pace is computed per game from the box score, with the same possession estimate as the season
-- figures (field-goal attempts + turnovers + 0.44 x free-throw attempts, averaged over the two
-- teams). A self-join on the game pairs the team's row with its opponent's. The inner query
-- takes the 10 most recent games; the outer query averages them. Aggregating first and limiting
-- afterwards would average the team's whole history, so the LIMIT belongs inside.
--
-- Parameters, in order:
--   1. team abbreviation
--   2. target game date (exclusive upper bound)
-- Run by: nba/features/extractors/game_context_features.py (_compute_game_features)
WITH recent AS (
    SELECT (t.fg_attempted + t.turnovers + 0.44 * t.ft_attempted
            + o.fg_attempted + o.turnovers + 0.44 * o.ft_attempted) / 2.0 AS pace,
           ABS(t.points - o.points) AS margin
    FROM team_game_logs t
    JOIN team_game_logs o
        ON o.game_id = t.game_id
        AND o.team_abbrev = t.opponent
    WHERE t.team_abbrev = %s
      AND t.game_date < %s
      AND t.fg_attempted IS NOT NULL
      AND o.fg_attempted IS NOT NULL
    ORDER BY t.game_date DESC, t.game_id DESC
    LIMIT 10
)
SELECT AVG(pace) AS avg_pace,
       AVG(margin) AS avg_margin,
       COUNT(*) AS games
FROM recent;
