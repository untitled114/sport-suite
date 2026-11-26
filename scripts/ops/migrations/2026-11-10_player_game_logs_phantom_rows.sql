-- Delete the phantom rows a 2026-11-08 23:37 ET backfill wrote into players.player_game_logs.
--
-- One statement (created_at 2026-11-09 04:37:17.394154+00) wrote 628 rows for Oct-Nov 2025 with
-- steals and 3PA NULL. 627 repeat the same player's game from the day before exactly (opponent,
-- points, minutes), on a date his team mostly didn't play: e.g. Kevin Durant "vs BKN" on 10-27 and
-- again on 10-28; HOU didn't play on the 28th. They count as extra games in early-season form.
-- The 628th has no matching previous-day game and is kept.
-- Back up all 628 first (a CSV copy kept with the backups).

BEGIN;

CREATE TEMP TABLE phantom ON COMMIT DROP AS
SELECT s.player_id, s.game_date
  FROM players.player_game_logs s
 WHERE s.created_at = timestamptz '2026-11-09 04:37:17.394154+00'
   AND s.steals IS NULL
   AND s.game_date BETWEEN '2025-10-21' AND '2025-11-30'
   AND EXISTS (SELECT 1 FROM players.player_game_logs p
                WHERE p.player_id = s.player_id AND p.game_date = s.game_date - 1
                  AND p.points = s.points AND p.minutes_played = s.minutes_played
                  AND p.opponent_abbrev = s.opponent_abbrev AND p.steals IS NOT NULL);

DO $$
DECLARE n integer;
BEGIN
    SELECT count(*) INTO n FROM phantom;
    IF n <> 627 THEN
        RAISE EXCEPTION 'refusing: % rows match, expected 627 (2026-11-10 count)', n;
    END IF;
END $$;

DELETE FROM players.player_game_logs l
 USING phantom p
 WHERE l.player_id = p.player_id AND l.game_date = p.game_date;

COMMIT;
