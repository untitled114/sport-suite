-- The five neutral-site games of 2024-25 were loaded wrong by the old games loader, which read
-- both "AWAY @ HOME" matchup rows as away rows at a neutral site (it now designates the home team).
-- In every one the home team's team_game_logs row names itself as its opponent and is
-- marked away; in three, games.games also shows home = away with the home team's score twice.
--
--   0022400147  2024-11-02  Mexico City             MIA 118 @ WAS 98     games + log
--   0022401230  2024-12-14  Neutral site, Cup semi  HOU 96 @ OKC 111     games + log
--   0022400633  2025-01-25  Paris                   IND 136 @ SAS 98     games + log
--   0022401229  2024-12-14  Neutral site, Cup semi  ATL 102 @ MIL 110    log only
--   0022400621  2025-01-23  Paris                   SAS 140 @ IND 110    log only
--
-- The away teams and scores are the away teams' own team_game_logs rows, and both sides agree with
-- players.player_game_logs (summed points, team and opponent, is_home). blowout_flag is generated
-- from the scores. start_time is filled afterwards by scripts/ops/backfill_games_start_time.py.

BEGIN;

DO $$
DECLARE found integer;
BEGIN
    SELECT count(*) INTO found FROM games.games
     WHERE (game_id, game_date, home_team, away_team, home_score, away_score) IN (
           ('0022400147', DATE '2024-11-02', 'WAS', 'WAS', 98, 98),
           ('0022401230', DATE '2024-12-14', 'OKC', 'OKC', 111, 111),
           ('0022400633', DATE '2025-01-25', 'SAS', 'SAS', 98, 98));
    IF found <> 3 THEN
        RAISE EXCEPTION 'refusing: % of the 3 games rows are as found', found;
    END IF;
    SELECT count(*) INTO found FROM games.team_game_logs
     WHERE (game_id, team_abbrev, opponent, is_home, points) IN (
           ('0022400147', 'WAS', 'WAS', false, 98),  ('0022400147', 'MIA', 'WAS', false, 118),
           ('0022401230', 'OKC', 'OKC', false, 111), ('0022401230', 'HOU', 'OKC', false, 96),
           ('0022400633', 'SAS', 'SAS', false, 98),  ('0022400633', 'IND', 'SAS', false, 136),
           ('0022401229', 'MIL', 'MIL', false, 110), ('0022401229', 'ATL', 'MIL', false, 102),
           ('0022400621', 'IND', 'IND', false, 110), ('0022400621', 'SAS', 'IND', false, 140));
    IF found <> 10 THEN
        RAISE EXCEPTION 'refusing: % of the 10 team_game_logs rows are as found', found;
    END IF;
END $$;

UPDATE games.games AS g
   SET away_team = v.away_team, away_score = v.away_score, updated_at = NOW()
  FROM (VALUES ('0022400147', 'MIA', 118), ('0022401230', 'HOU', 96), ('0022400633', 'IND', 136))
       AS v (game_id, away_team, away_score)
 WHERE g.game_id = v.game_id AND g.home_team = g.away_team;

UPDATE games.team_game_logs AS t
   SET opponent = v.opponent, is_home = true
  FROM (VALUES ('0022400147', 'WAS', 'MIA'), ('0022401230', 'OKC', 'HOU'), ('0022400633', 'SAS', 'IND'),
               ('0022401229', 'MIL', 'ATL'), ('0022400621', 'IND', 'SAS'))
       AS v (game_id, team_abbrev, opponent)
 WHERE t.game_id = v.game_id AND t.team_abbrev = v.team_abbrev AND t.opponent = t.team_abbrev;

DO $$
DECLARE bad integer;
BEGIN
    SELECT count(*) INTO bad FROM games.games WHERE home_team = away_team;
    IF bad > 0 THEN
        RAISE EXCEPTION 'refusing: % games rows still have home = away', bad;
    END IF;
    SELECT count(*) INTO bad FROM games.team_game_logs WHERE opponent = team_abbrev;
    IF bad > 0 THEN
        RAISE EXCEPTION 'refusing: % team_game_logs rows still name their own team', bad;
    END IF;
    -- Each repaired game: one home and one away log row, each the other's opponent, matching games.
    SELECT count(*) INTO bad FROM games.games g
     WHERE g.game_id IN ('0022400147', '0022401230', '0022400633', '0022401229', '0022400621')
       AND NOT (EXISTS (SELECT 1 FROM games.team_game_logs t WHERE t.game_id = g.game_id
                         AND t.team_abbrev = g.home_team AND t.opponent = g.away_team
                         AND t.is_home AND t.points = g.home_score)
            AND EXISTS (SELECT 1 FROM games.team_game_logs t WHERE t.game_id = g.game_id
                         AND t.team_abbrev = g.away_team AND t.opponent = g.home_team
                         AND NOT t.is_home AND t.points = g.away_score));
    IF bad > 0 THEN
        RAISE EXCEPTION 'refusing: % repaired games disagree with their team logs', bad;
    END IF;
END $$;

COMMIT;
