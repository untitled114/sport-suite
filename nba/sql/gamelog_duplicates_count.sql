-- How many duplicate game-log rows the clean-up would delete (dry-run count).
--
-- Same selection as gamelog_duplicates_delete.sql, ending in a count. For each (player, date)
-- with more than one row, one row is kept, chosen in this order:
--   1. a row with believable minutes (under 60) before one without;
--   2. a row with steals, blocks and turnovers filled before one with gaps;
--   3. the oldest row (lowest game_log_id).
-- DISTINCT ON with a matching ORDER BY keeps that one row per group.
--
-- Parameters: none.
-- Run by: nba/scripts/deduplicate_player_gamelogs.py (deduplicate_gamelogs)
WITH duplicates AS (
    -- Find all player-game pairs with duplicates
    SELECT player_id, game_date
    FROM player_game_logs
    GROUP BY player_id, game_date
    HAVING COUNT(*) > 1
),
ranked_entries AS (
    -- Rank entries within each duplicate group
    SELECT
        pgl.game_log_id,
        pgl.player_id,
        pgl.game_date,
        pgl.minutes_played,
        pgl.steals,
        pgl.blocks,
        pgl.turnovers,
        -- Prefer entries with valid minutes (< 60)
        CASE
            WHEN pgl.minutes_played < 60 THEN 1
            ELSE 2
        END as minutes_rank,
        -- Prefer entries with complete stats (non-null steals/blocks/turnovers)
        CASE
            WHEN pgl.steals IS NOT NULL AND pgl.blocks IS NOT NULL
                 AND pgl.turnovers IS NOT NULL THEN 1
            ELSE 2
        END as completeness_rank,
        -- Use game_log_id as tiebreaker (keep oldest)
        pgl.game_log_id as id_rank
    FROM player_game_logs pgl
    INNER JOIN duplicates d ON pgl.player_id = d.player_id AND pgl.game_date = d.game_date
),
entries_to_keep AS (
    -- Select the best entry from each duplicate group
    SELECT DISTINCT ON (player_id, game_date)
        game_log_id
    FROM ranked_entries
    ORDER BY player_id, game_date, minutes_rank, completeness_rank, id_rank
),
entries_to_delete AS (
    -- All duplicate entries except the ones we're keeping
    SELECT pgl.game_log_id
    FROM player_game_logs pgl
    INNER JOIN duplicates d ON pgl.player_id = d.player_id AND pgl.game_date = d.game_date
    WHERE pgl.game_log_id NOT IN (SELECT game_log_id FROM entries_to_keep)
)
SELECT COUNT(*) FROM entries_to_delete;
