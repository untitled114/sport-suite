-- players.player_game_logs.season becomes the END year for every season, as a function of
-- game_date: October or later -> year + 1, otherwise the year (2025-26 = 2026).
--
-- Before: 2020-21 .. 2024-25 were labelled with the start year (2020 .. 2024), 2025-26 held both
-- (2025 from an nba_api loader, 2026 from fetch_daily_stats), 2026-27 the end year (2027). Every
-- other table (team_game_logs, team_season_stats, player_season_stats) uses the end year.
-- The feature extractor's previous-season lookup switches to the end year in the same
-- deploy, so it selects the same games as before.
-- Back up (player_id, game_date, season) first (a CSV copy kept with the backups).

BEGIN;

UPDATE players.player_game_logs
   SET season = CASE WHEN EXTRACT(MONTH FROM game_date) >= 10
                     THEN EXTRACT(YEAR FROM game_date)::int + 1
                     ELSE EXTRACT(YEAR FROM game_date)::int END
 WHERE season IS DISTINCT FROM CASE WHEN EXTRACT(MONTH FROM game_date) >= 10
                                    THEN EXTRACT(YEAR FROM game_date)::int + 1
                                    ELSE EXTRACT(YEAR FROM game_date)::int END;

DO $$
DECLARE bad integer; overlap integer;
BEGIN
    SELECT count(*) INTO bad FROM players.player_game_logs
     WHERE season <> CASE WHEN EXTRACT(MONTH FROM game_date) >= 10
                          THEN EXTRACT(YEAR FROM game_date)::int + 1
                          ELSE EXTRACT(YEAR FROM game_date)::int END;
    -- each season's dates must not overlap the next one's
    SELECT count(*) INTO overlap FROM (
        SELECT season, max(game_date) AS last, lead(min(game_date)) OVER (ORDER BY season) AS next_first
          FROM players.player_game_logs GROUP BY season) s
     WHERE s.next_first IS NOT NULL AND s.next_first <= s.last;
    IF bad > 0 OR overlap > 0 THEN
        RAISE EXCEPTION 'refusing: % rows off the rule, % overlapping seasons', bad, overlap;
    END IF;
END $$;

COMMIT;
