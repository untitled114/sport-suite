-- A player's last-5, last-10 and last-20 game averages of one stat, its spread, and his team.
--
-- Games are numbered newest first with ROW_NUMBER(); each average is the same AVG with a
-- different FILTER, so the three windows come from one pass. An optional as-of date keeps the
-- windows strictly before a game, so a projection never sees the game it is for.
--
-- Placeholders in braces are identifiers the code fills from fixed lists, never from input:
--   {table}: batter_game_logs or pitcher_game_logs
--   {stat_col}: the stat's column in that table
-- Parameters, in order:
--   1. player full name
--   2. as-of date, or NULL for every game played
--   3. the same as-of date
-- Run by: mlb/models/projection_model.py (_get_player_rolling_stats)
WITH recent AS (
    SELECT {stat_col} AS val, team,
           ROW_NUMBER() OVER (ORDER BY game_date DESC, game_id DESC) AS rn
    FROM {table}
    WHERE unaccent(full_name) = unaccent(%s)
      AND {stat_col} IS NOT NULL
      AND (%s::date IS NULL OR game_date < %s::date)
)
SELECT
    AVG(val) FILTER (WHERE rn <= 5)  AS l5,
    AVG(val) FILTER (WHERE rn <= 10) AS l10,
    AVG(val) FILTER (WHERE rn <= 20) AS l20,
    STDDEV(val) FILTER (WHERE rn <= 20) AS std,
    MAX(team) FILTER (WHERE rn = 1)  AS team
FROM recent;
