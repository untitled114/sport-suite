-- The ten player-days with the most duplicate game-log rows.
--
-- A player has one game log per day. This lists the worst offenders with the minutes each
-- duplicate row reports (array_agg), so a reader can see what kind of duplicate it is before
-- anything is deleted.
--
-- Parameters: none.
-- Run by: nba/scripts/deduplicate_player_gamelogs.py (analyze_duplicates)
SELECT
    pp.full_name,
    pgl.game_date,
    COUNT(*) AS dup_count,
    array_agg(pgl.minutes_played ORDER BY pgl.minutes_played) AS minutes
FROM player_game_logs pgl
JOIN player_profile pp ON pgl.player_id = pp.player_id
GROUP BY pgl.player_id, pp.full_name, pgl.game_date
HAVING COUNT(*) > 1
ORDER BY dup_count DESC
LIMIT 10;
