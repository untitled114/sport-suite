-- Home runs and hits per finished game, labelled with the home team's park.
--
-- One row per game: every batter's home runs and hits in that game, summed, with the home
-- team. The caller turns these per-game rows into park factors.
--
-- Parameters, in order:
--   1. game statuses that count as finished (text array, lower case)
--   2. first season year
--   3. last season year
-- Run by: mlb/scripts/loaders/compute_park_factors.py (_fetch_batting_rows)
SELECT g.home_team,
       COALESCE(SUM(bgl.home_runs), 0) AS hr,
       COALESCE(SUM(bgl.hits), 0)      AS hits
  FROM mlb_games.games g
  JOIN mlb_players.batter_game_logs bgl ON bgl.game_id = g.game_id
 WHERE LOWER(g.status) = ANY(%s)
   AND g.home_score IS NOT NULL
   AND EXTRACT(YEAR FROM g.game_date)::int BETWEEN %s AND %s
 GROUP BY g.game_id, g.home_team;
