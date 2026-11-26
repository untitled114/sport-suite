-- Add new players and refresh the team of known ones, without ever renaming a player.
--
-- A stored name is a join key for other tables, so the update touches only the team (and
-- keeps the old team when the new value is NULL). Written with execute_values.
--
-- Parameters, in order:
--   1. the rows (player_id, full_name, team_abbrev)
-- Run by: nba/scripts/loaders/load_nba_players.py (load_current_season_profiles)
INSERT INTO player_profile (player_id, full_name, team_abbrev)
VALUES %s
ON CONFLICT (player_id) DO UPDATE SET
    -- Existing names are join keys for other tables: never rename here.
    team_abbrev = COALESCE(EXCLUDED.team_abbrev, player_profile.team_abbrev),
    updated_at = NOW();
