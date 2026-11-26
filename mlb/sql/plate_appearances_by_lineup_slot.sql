-- Average plate appearances per batting-order slot over a range of seasons.
--
-- Joins each batter's lineup slot to his game log for the same game and averages the plate
-- appearances per slot (1 to 9). The count per slot is returned with it.
--
-- Parameters, in order:
--   1. first season year
--   2. last season year
-- Run by: mlb/scripts/loaders/compute_expected_pa.py (_fetch_slot_means)
SELECT bls.batting_order AS slot,
       AVG(bgl.plate_appearances)::numeric AS mean_pa,
       COUNT(*) AS n
  FROM mlb_players.batter_lineup_slots bls
  JOIN mlb_players.batter_game_logs bgl
    ON bls.player_id = bgl.player_id AND bls.game_id = bgl.game_id
 WHERE bls.batting_order BETWEEN 1 AND 9
   AND bgl.plate_appearances IS NOT NULL
   AND EXTRACT(YEAR FROM bls.game_date)::int BETWEEN %s AND %s
 GROUP BY bls.batting_order
 ORDER BY bls.batting_order;
