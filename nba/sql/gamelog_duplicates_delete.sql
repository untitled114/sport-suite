-- Delete duplicate game-log rows, keeping the best row of each (player, date).
--
-- The row kept per group: believable minutes first, then complete defensive stats, then the
-- oldest row. See gamelog_duplicates_count.sql for the same selection as a dry-run count.
--
-- Parameters: none.
-- Run by: nba/scripts/deduplicate_player_gamelogs.py (deduplicate_gamelogs)
WITH duplicates AS (
    SELECT player_id, game_date
    FROM player_game_logs
    GROUP BY player_id, game_date
    HAVING COUNT(*) > 1
),
ranked_entries AS (
    SELECT
        pgl.game_log_id,
        pgl.player_id,
        pgl.game_date,
        CASE
            WHEN pgl.minutes_played < 60 THEN 1
            ELSE 2
        END as minutes_rank,
        CASE
            WHEN pgl.steals IS NOT NULL AND pgl.blocks IS NOT NULL
                 AND pgl.turnovers IS NOT NULL THEN 1
            ELSE 2
        END as completeness_rank,
        pgl.game_log_id as id_rank
    FROM player_game_logs pgl
    INNER JOIN duplicates d ON pgl.player_id = d.player_id AND pgl.game_date = d.game_date
),
entries_to_keep AS (
    SELECT DISTINCT ON (player_id, game_date)
        game_log_id
    FROM ranked_entries
    ORDER BY player_id, game_date, minutes_rank, completeness_rank, id_rank
)
DELETE FROM player_game_logs
WHERE game_log_id IN (
    SELECT pgl.game_log_id
    FROM player_game_logs pgl
    INNER JOIN duplicates d ON pgl.player_id = d.player_id AND pgl.game_date = d.game_date
    WHERE pgl.game_log_id NOT IN (SELECT game_log_id FROM entries_to_keep)
);
