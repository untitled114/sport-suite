-- A batter's totals over his last N games before a date.
--
-- The inner query takes the newest N game logs before the target date; the outer query sums
-- them. COUNT(doubles) counts only the rows where the extra-base columns are filled, so the
-- caller knows whether the weighted on-base figure can be computed in full.
--
-- Parameters, in order:
--   1. batter full name
--   2. target game date (exclusive upper bound)
--   3. N, the number of most recent games
-- Run by: mlb/features/extractors/sabermetric_features.py (_extract_batter)
SELECT
    COUNT(*)::float                          AS games,
    COALESCE(SUM(bgl.at_bats), 0)            AS ab,
    COALESCE(SUM(bgl.hits), 0)               AS h,
    COALESCE(SUM(bgl.total_bases), 0)        AS tb,
    COALESCE(SUM(bgl.walks), 0)              AS bb,
    COALESCE(SUM(bgl.strikeouts), 0)         AS k,
    COALESCE(SUM(bgl.plate_appearances), 0)  AS pa,
    COALESCE(SUM(bgl.home_runs), 0)          AS hr,
    COALESCE(SUM(bgl.doubles), 0)            AS doubles,
    COALESCE(SUM(bgl.triples), 0)            AS triples,
    COALESCE(SUM(bgl.hit_by_pitch), 0)       AS hbp,
    COALESCE(SUM(bgl.intentional_walks), 0)  AS ibb,
    COALESCE(SUM(bgl.sac_flies), 0)          AS sf,
    COUNT(bgl.doubles)                       AS xbh_rows
FROM (
    SELECT bgl.at_bats, bgl.hits, bgl.total_bases, bgl.walks,
           bgl.strikeouts, bgl.plate_appearances, bgl.home_runs,
           bgl.doubles, bgl.triples, bgl.hit_by_pitch,
           bgl.intentional_walks, bgl.sac_flies
    FROM batter_game_logs bgl
    JOIN batter_profile bp ON bgl.player_id = bp.player_id
    WHERE unaccent(bp.full_name) = unaccent(%s)
      AND bgl.game_date < %s
    ORDER BY bgl.game_date DESC, bgl.game_id DESC
    LIMIT %s
) bgl;
