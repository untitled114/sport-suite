-- Fill the batter-versus-pitcher history table from the per-game encounters.
--
-- The caller empties the table first and runs this in the same transaction, so readers see
-- either the old history or the new one, never a partial table.
-- One row per (batter, pitcher) with career totals and three rates. Each rate is guarded by a
-- CASE so a pair with no qualifying plate appearances gets NULL, not a division by zero.
--
-- Parameters: none.
-- Run by: mlb/scripts/loaders/load_statcast_pitchmix.py (rebuild_matchup_history)
INSERT INTO matchup_history
(batter_id, pitcher_id, last_game_date, pa, hits, k, bb, tb,
 avg, ops_proxy, k_rate)
SELECT
    batter_id,
    pitcher_id,
    MAX(game_date) AS last_game_date,
    SUM(pa)        AS pa,
    SUM(hits)      AS hits,
    SUM(k)         AS k,
    SUM(bb)        AS bb,
    SUM(tb)        AS tb,
    CASE WHEN SUM(GREATEST(pa - bb, 0)) > 0
         THEN SUM(hits)::numeric / SUM(GREATEST(pa - bb, 0)) END AS avg,
    CASE WHEN SUM(pa) > 0
         THEN (SUM(hits) + SUM(bb))::numeric / SUM(pa)
              + SUM(tb)::numeric / SUM(pa) END AS ops_proxy,
    CASE WHEN SUM(pa) > 0
         THEN SUM(k)::numeric / SUM(pa) END AS k_rate
FROM bvp_encounters
GROUP BY batter_id, pitcher_id;
