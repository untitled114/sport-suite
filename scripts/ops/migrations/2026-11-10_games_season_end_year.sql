-- games.games.season becomes the END year for every season (October or later -> year + 1), and
-- every game gets a season_type.
--
-- Before: 2021-22 .. 2024-25 were labelled with the start year (2021 .. 2024) and no season_type;
-- 2025-26 and 2026-27 already used the end year, with season_type set by the games loader. Each
-- older season holds exactly 1,230 games (82 x 30 / 2): regular season only, so they are typed
-- 'Regular Season'. The 2025-12-16 NYK-SAS game (401809839) is the NBA Cup final, which doesn't
-- count in the standings: 'NBA Cup'. Rows not yet matched to a league game id are 2026-27
-- regular-season games. Back up (game_id, season, season_type) first.

BEGIN;

UPDATE games.games
   SET season = CASE WHEN EXTRACT(MONTH FROM game_date) >= 10
                     THEN EXTRACT(YEAR FROM game_date)::int + 1
                     ELSE EXTRACT(YEAR FROM game_date)::int END,
       updated_at = NOW()
 WHERE season IS DISTINCT FROM CASE WHEN EXTRACT(MONTH FROM game_date) >= 10
                                    THEN EXTRACT(YEAR FROM game_date)::int + 1
                                    ELSE EXTRACT(YEAR FROM game_date)::int END;

-- 'NBA Cup' joins the three types the games loader writes. The loader does not type a Cup
-- final, so this migration does.
ALTER TABLE games.games DROP CONSTRAINT IF EXISTS games_season_type_check;
ALTER TABLE games.games ADD CONSTRAINT games_season_type_check
    CHECK (season_type IN ('Regular Season', 'PlayIn', 'Playoffs', 'NBA Cup'));

UPDATE games.games SET season_type = 'NBA Cup', updated_at = NOW()
 WHERE game_id = '401809839' AND season_type IS NULL;

UPDATE games.games SET season_type = 'Regular Season', updated_at = NOW()
 WHERE season_type IS NULL
   AND (game_date < '2025-07-01' OR game_id LIKE 'TBD2026111%');

DO $$
DECLARE bad integer; untyped integer; per_season integer;
BEGIN
    SELECT count(*) INTO bad FROM games.games
     WHERE season <> CASE WHEN EXTRACT(MONTH FROM game_date) >= 10
                          THEN EXTRACT(YEAR FROM game_date)::int + 1
                          ELSE EXTRACT(YEAR FROM game_date)::int END;
    SELECT count(*) INTO untyped FROM games.games WHERE season_type IS NULL;
    SELECT count(*) INTO per_season FROM (
        SELECT season FROM games.games WHERE season BETWEEN 2022 AND 2025
         GROUP BY season HAVING count(*) FILTER (WHERE season_type = 'Regular Season') <> 1230) s;
    IF bad > 0 OR untyped > 0 OR per_season > 0 THEN
        RAISE EXCEPTION 'refusing: % rows off the season rule, % untyped, % older seasons not 1,230 regular-season games',
            bad, untyped, per_season;
    END IF;
END $$;

COMMIT;
