-- games.team_game_logs' per-game possessions, pace and ratings for 2021-22 .. 2024-25, recomputed
-- from the box columns.
--
-- The first load left placeholders on all 9,840 rows of those seasons: possessions 100 on every
-- row, defensive_rating 110.00 on every row, offensive_rating equal to the team's points, and a pace
-- of 155-235. Run after 2026-11-11_team_game_logs_box_from_players.sql. The definition is the one
-- used for the season figures (there is no offensive-rebound column), per game:
--   a team's possessions  = FGA + TOV + 0.44 * FTA                       (stored rounded: integer)
--   pace                  = (team's + opponent's possessions) / 2        (no overtime adjustment:
--                           team_game_logs has no minutes, so an overtime game runs above 140;
--                           the 176-175 SAC-LAC double overtime of 2023-02-24 is 140.1)
--   offensive_rating      = points / pace * 100
--   defensive_rating      = opponent's points / pace * 100
-- only where both teams' box columns add up to their points (2*FGM + 3PM + FTM); every other row of
-- these seasons gets NULL in the four columns, not the placeholder. 2025-26 and 2026-27 are left as
-- they are (NULL: the current loader doesn't write these columns).
-- Back up (game_log_id, possessions, pace, offensive_rating, defensive_rating) first.

BEGIN;

CREATE TEMP TABLE recomputed ON COMMIT DROP AS
SELECT t.game_log_id,
       t.fg_attempted + t.turnovers + 0.44 * t.ft_attempted AS team_poss,
       ((t.fg_attempted + t.turnovers + 0.44 * t.ft_attempted)
        + (o.fg_attempted + o.turnovers + 0.44 * o.ft_attempted)) / 2.0 AS game_pace,
       t.points, o.points AS opp_points
  FROM games.team_game_logs t
  JOIN games.team_game_logs o ON o.game_id = t.game_id AND o.team_abbrev = t.opponent
 WHERE t.season BETWEEN 2022 AND 2025
   AND t.points = 2 * t.fg_made + t.three_pt_made + t.ft_made
   AND o.points = 2 * o.fg_made + o.three_pt_made + o.ft_made;

UPDATE games.team_game_logs t
   SET possessions = ROUND(r.team_poss)::int,
       pace = ROUND(r.game_pace, 2),
       offensive_rating = ROUND(r.points / r.game_pace * 100, 2),
       defensive_rating = ROUND(r.opp_points / r.game_pace * 100, 2)
  FROM recomputed r
 WHERE t.game_log_id = r.game_log_id;

UPDATE games.team_game_logs t
   SET possessions = NULL, pace = NULL, offensive_rating = NULL, defensive_rating = NULL
 WHERE t.season BETWEEN 2022 AND 2025
   AND NOT EXISTS (SELECT 1 FROM recomputed r WHERE r.game_log_id = t.game_log_id);

DO $$
DECLARE placeholders integer; implausible integer;
BEGIN
    SELECT count(*) INTO placeholders FROM games.team_game_logs
     WHERE season BETWEEN 2022 AND 2025 AND (possessions = 100 AND defensive_rating = 110.00);
    SELECT count(*) INTO implausible FROM games.team_game_logs
     WHERE season BETWEEN 2022 AND 2025 AND pace IS NOT NULL
       AND (pace NOT BETWEEN 80 AND 170 OR offensive_rating NOT BETWEEN 60 AND 180
            OR defensive_rating NOT BETWEEN 60 AND 180);
    IF placeholders > 0 OR implausible > 0 THEN
        RAISE EXCEPTION 'refusing: % placeholder rows left, % implausible values', placeholders, implausible;
    END IF;
END $$;

SELECT season, count(*) AS team_games, count(pace) AS recomputed, count(*) - count(pace) AS nulled,
       ROUND(AVG(pace), 1) AS avg_pace, ROUND(AVG(offensive_rating), 1) AS avg_ortg
  FROM games.team_game_logs WHERE season BETWEEN 2022 AND 2025 GROUP BY 1 ORDER BY 1;

COMMIT;
