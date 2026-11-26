-- games.season_type: which part of the season a game belongs to. Regular season, play-in and
-- playoff games share games.season, so team records computed from it counted the postseason
-- (2025-26: 20 of 30 teams off; CLE 60-40 over 100 games vs 52-30). The games loader writes the
-- type it fetched each game under. NULL = not loaded as one of these (e.g. the NBA Cup final,
-- which counts toward no record), or loaded before this column existed.
ALTER TABLE games.games ADD COLUMN IF NOT EXISTS season_type TEXT;
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'games_season_type_check') THEN
        ALTER TABLE games.games ADD CONSTRAINT games_season_type_check
            CHECK (season_type IN ('Regular Season', 'PlayIn', 'Playoffs'));
    END IF;
END $$;
