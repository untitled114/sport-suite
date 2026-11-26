-- games.team_game_logs.season becomes the END year for every season (October or later -> year + 1),
-- the convention games.games and players.player_game_logs moved to earlier on 2026-11-10.
--
-- Before: 2021-22 .. 2024-25 carried the start year (2021 .. 2024, 2,460 rows each); 2025-26 and
-- 2026-27 already carried the end year (2026, 2027), as load_nba_games_incremental writes them.
-- So 2024-25 and 2025-26 were 2024 and 2026, and calculate_team_stats --season 2025 found no rows.
-- No game falls in July-September. season is in no key; idx_tgl_season is rebuilt by the UPDATE.
-- Back up (team_abbrev, game_id, game_date, season) first.

BEGIN;

UPDATE games.team_game_logs
   SET season = CASE WHEN EXTRACT(MONTH FROM game_date) >= 10
                     THEN EXTRACT(YEAR FROM game_date)::int + 1
                     ELSE EXTRACT(YEAR FROM game_date)::int END
 WHERE season IS DISTINCT FROM CASE WHEN EXTRACT(MONTH FROM game_date) >= 10
                                    THEN EXTRACT(YEAR FROM game_date)::int + 1
                                    ELSE EXTRACT(YEAR FROM game_date)::int END;

DO $$
DECLARE bad integer; mismatched integer; per_season integer;
BEGIN
    SELECT count(*) INTO bad FROM games.team_game_logs
     WHERE season IS DISTINCT FROM CASE WHEN EXTRACT(MONTH FROM game_date) >= 10
                                        THEN EXTRACT(YEAR FROM game_date)::int + 1
                                        ELSE EXTRACT(YEAR FROM game_date)::int END;
    -- Every log row now carries its game's season
    SELECT count(*) INTO mismatched FROM games.team_game_logs t
      JOIN games.games g USING (game_id)
     WHERE t.season <> g.season;
    -- The older seasons keep their 2,460 rows (1,230 games x 2), each under its end year
    SELECT count(*) INTO per_season FROM (
        SELECT s FROM generate_series(2022, 2025) s
         WHERE (SELECT count(*) FROM games.team_game_logs WHERE season = s) <> 2460) x;
    IF bad > 0 OR mismatched > 0 OR per_season > 0 THEN
        RAISE EXCEPTION 'refusing: % rows off the season rule, % unlike their game, % older seasons not 2,460 rows',
            bad, mismatched, per_season;
    END IF;
END $$;

COMMIT;
