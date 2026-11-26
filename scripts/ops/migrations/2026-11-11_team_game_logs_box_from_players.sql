-- games.team_game_logs' box columns, rebuilt from players.player_game_logs where they don't add up.
--
-- In 2021-22 .. 2023-24, and on 125 rows of 2024-25, a team-game's field goals, threes and free
-- throws don't add up to its points (2*FGM + 3PM + FTM <> PTS), while its points match ESPN's final
-- score. Those columns are sums of player rows that were missing (2022-23 had 35% of team-games
-- complete). After scripts/ops/backfill_player_box_scores.py has added the missing player rows,
-- this rewrites fg_made, fg_attempted, three_pt_made, three_pt_attempted, ft_made, ft_attempted,
-- rebounds, assists and turnovers from the player rows, only on a team-game whose columns don't add
-- up today, whose player rows sum to the team's points, and whose rebuilt columns add up exactly.
-- Every other row is left as it is. possessions, pace and the ratings are not touched.
-- Back up the box columns of seasons 2022 .. 2025 first.

BEGIN;

CREATE TEMP TABLE rebuilt ON COMMIT DROP AS
WITH p AS (
    SELECT game_date, team_abbrev,
           sum(points) AS pts, sum(fg_made) AS fgm, sum(fg_attempted) AS fga,
           sum(three_pointers_made) AS tpm, sum(three_pt_attempted) AS tpa,
           sum(ft_made) AS ftm, sum(ft_attempted) AS fta,
           sum(rebounds) AS reb, sum(assists) AS ast, sum(turnovers) AS tov
      FROM players.player_game_logs
     WHERE game_date BETWEEN '2021-10-01' AND '2025-07-01'
     GROUP BY 1, 2)
SELECT t.game_log_id, p.*
  FROM games.team_game_logs t
  JOIN p USING (game_date, team_abbrev)
 WHERE t.season BETWEEN 2022 AND 2025
   AND t.points IS DISTINCT FROM 2 * t.fg_made + t.three_pt_made + t.ft_made
   AND p.pts = t.points
   AND 2 * p.fgm + p.tpm + p.ftm = t.points;

UPDATE games.team_game_logs t
   SET fg_made = r.fgm, fg_attempted = r.fga, three_pt_made = r.tpm, three_pt_attempted = r.tpa,
       ft_made = r.ftm, ft_attempted = r.fta, rebounds = r.reb, assists = r.ast, turnovers = r.tov
  FROM rebuilt r
 WHERE t.game_log_id = r.game_log_id;

DO $$
DECLARE bad integer;
BEGIN
    SELECT count(*) INTO bad
      FROM games.team_game_logs t JOIN rebuilt r USING (game_log_id)
     WHERE t.points <> 2 * t.fg_made + t.three_pt_made + t.ft_made;
    IF bad > 0 THEN
        RAISE EXCEPTION 'refusing: % rebuilt rows do not add up', bad;
    END IF;
END $$;

SELECT t.season, count(r.game_log_id) AS rebuilt,
       count(*) FILTER (WHERE t.points <> 2 * t.fg_made + t.three_pt_made + t.ft_made) AS still_off
  FROM games.team_game_logs t LEFT JOIN rebuilt r USING (game_log_id)
 WHERE t.season BETWEEN 2022 AND 2025
 GROUP BY 1 ORDER BY 1;

COMMIT;
