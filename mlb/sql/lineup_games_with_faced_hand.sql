-- A team's batting lines over the last 60 days, each tagged with the hand of the pitcher faced.
--
-- The team-level counterpart of batter_games_with_opposing_hand.sql: every batter-game of one
-- team in a 60-day window before the target date, with the opposing main pitcher's hand from
-- a LATERAL subquery.
--
-- Parameters, in order:
--   1. team abbreviation
--   2. target game date (exclusive upper bound)
--   3. the same date, for the start of the 60-day window
-- Run by: mlb/features/extractors/platoon_features.py (_opp_lineup_splits)
SELECT bgl.at_bats, bgl.hits, bgl.total_bases, bgl.walks,
       bgl.strikeouts, bgl.plate_appearances, opp.throws AS faced_hand
FROM batter_game_logs bgl
JOIN LATERAL (
    SELECT pp.throws
    FROM pitcher_game_logs sp
    JOIN pitcher_profile pp ON sp.player_id = pp.player_id
    WHERE sp.game_id = bgl.game_id AND sp.team = bgl.opponent
    ORDER BY sp.batters_faced DESC NULLS LAST
    LIMIT 1
) opp ON TRUE
WHERE bgl.team = %s
  AND bgl.game_date < %s
  AND bgl.game_date >= (%s::date - INTERVAL '60 days');
