-- Add the players missing from players.player_profile whose box scores ESPN serves, so
-- fetch_daily_stats can file their games.
--
-- Source: stats.nba.com CommonAllPlayers (IsOnlyCurrentSeason=1), read 2026-11-10 14:35 ET:
--   * season 2025-26 (players whose last season was 2025-26): 185 players, 13 missing here;
--   * season 2026-27: 526 players, 1 missing (Richie Saunders).
-- Ids are NBA ids. Insert only: no existing row changes.
-- team_abbrev is a player's CURRENT team (rosters are built from it), so the 13 players on no
-- 2026-27 roster get NULL even when the 2025-26 list names a team; Saunders gets MEM.

BEGIN;

INSERT INTO players.player_profile (player_id, full_name, team_abbrev)
VALUES
    (1630828, 'Alex Antetokounmpo', NULL),
    (1642380, 'Adama Bal', NULL),
    (1642468, 'Darius Brown II', NULL),
    (1631451, 'Javonte Cooke', NULL),
    (1643158, 'Andersson Garcia', NULL),
    (1643060, 'Hayden Gray', NULL),
    (1642396, 'Blake Hinson', NULL),
    (1643016, 'Bez Mbeng', NULL),
    (1642490, 'Josh Oduro', NULL),
    (1643253, 'Toby Okani', NULL),
    (1642955, 'Kadary Richmond', NULL),
    (1642362, 'Payton Sandfort', NULL),
    (1631351, 'Lucas Williamson', NULL),
    (1643563, 'Richie Saunders', 'MEM')
ON CONFLICT (player_id) DO NOTHING;

DO $$
DECLARE n integer; clash integer;
BEGIN
    SELECT count(*) INTO n FROM players.player_profile
     WHERE player_id IN (1630828, 1642380, 1642468, 1631451, 1643158, 1643060, 1642396, 1643016,
                         1642490, 1643253, 1642955, 1642362, 1631351, 1643563);
    IF n <> 14 THEN
        RAISE EXCEPTION 'expected 14 profiles after insert, found %', n;
    END IF;
    -- A new name must not collide with an existing player's name key (fetch_daily_stats
    -- matches by name).
    SELECT count(*) INTO clash
      FROM players.player_profile a JOIN players.player_profile b
        ON a.player_id <> b.player_id AND players.name_key(a.full_name) = players.name_key(b.full_name)
     WHERE a.player_id IN (1630828, 1642380, 1642468, 1631451, 1643158, 1643060, 1642396, 1643016,
                           1642490, 1643253, 1642955, 1642362, 1631351, 1643563);
    IF clash > 0 THEN
        RAISE EXCEPTION 'refusing: % new profiles share a name key with an existing player', clash;
    END IF;
END $$;

COMMIT;
