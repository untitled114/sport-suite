-- Every player profile with how many game logs it has.
--
-- Used to resolve a name that matches more than one profile: within a name, the profile with
-- the most games sorts first. The LEFT JOIN keeps profiles that have no game yet (count 0).
--
-- Parameters: none.
-- Run by: nba/utils/name_normalizer.py (_build_from_database)
SELECT
    pp.full_name,
    pp.player_id,
    COALESCE(game_counts.game_count, 0) as game_count
FROM player_profile pp
LEFT JOIN (
    SELECT player_id, COUNT(*) as game_count
    FROM player_game_logs
    GROUP BY player_id
) game_counts ON pp.player_id = game_counts.player_id
ORDER BY pp.full_name, game_count DESC;
