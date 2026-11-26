-- A batter's recent games, each tagged with the throwing hand of the pitcher he mostly faced.
--
-- The game log does not say who a batter faced. For each of his games a LATERAL subquery takes
-- the opposing pitcher who faced the most batters that day (the starter, in practice) and
-- returns his throwing hand. The caller splits the totals by that hand.
-- Names are compared through unaccent() because profile names carry diacritics.
-- Only games before the target date are read, so a feature never sees its own game.
--
-- Parameters, in order:
--   1. batter full name
--   2. target game date (exclusive upper bound)
-- Run by: mlb/features/extractors/platoon_features.py (_batter_full_splits)
SELECT bgl.at_bats, bgl.hits, bgl.total_bases, bgl.walks,
       bgl.strikeouts, bgl.plate_appearances, opp.throws AS opp_hand
FROM batter_game_logs bgl
JOIN batter_profile bp ON bgl.player_id = bp.player_id
JOIN LATERAL (
    SELECT pp.throws
    FROM pitcher_game_logs sp
    JOIN pitcher_profile pp ON sp.player_id = pp.player_id
    WHERE sp.game_id = bgl.game_id AND sp.team = bgl.opponent
    ORDER BY sp.batters_faced DESC NULLS LAST
    LIMIT 1
) opp ON TRUE
WHERE unaccent(bp.full_name) = unaccent(%s)
  AND bgl.game_date < %s
ORDER BY bgl.game_date DESC, bgl.game_id DESC
LIMIT 400;
