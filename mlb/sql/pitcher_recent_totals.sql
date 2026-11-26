-- A pitcher's totals over his last N games before a date.
--
-- The inner query takes the newest N game logs before the target date; the outer query sums
-- them. COALESCE turns an empty window into zeros, and the game count tells the caller how
-- much history the totals rest on.
--
-- Parameters, in order:
--   1. pitcher full name
--   2. target game date (exclusive upper bound)
--   3. N, the number of most recent games
-- Run by: mlb/features/extractors/sabermetric_features.py (_extract_pitcher)
SELECT
    COUNT(*)::float                              AS games,
    COALESCE(SUM(pgl.innings_pitched), 0)        AS ip,
    COALESCE(SUM(pgl.strikeouts), 0)             AS k,
    COALESCE(SUM(pgl.walks), 0)                  AS bb,
    COALESCE(SUM(pgl.batters_faced), 0)          AS bf,
    COALESCE(SUM(pgl.home_runs_allowed), 0)      AS hr
FROM (
    SELECT pgl.innings_pitched, pgl.strikeouts, pgl.walks,
           pgl.batters_faced, pgl.home_runs_allowed
    FROM pitcher_game_logs pgl
    JOIN pitcher_profile pp ON pgl.player_id = pp.player_id
    WHERE unaccent(pp.full_name) = unaccent(%s)
      AND pgl.game_date < %s
    ORDER BY pgl.game_date DESC, pgl.game_id DESC
    LIMIT %s
) pgl;
