-- games.blowout_flag reads NULL (unknown) for a game without both scores, instead of FALSE
-- ("not a blowout"). A generated column's expression can't be altered
-- in place, so the column is re-added (its partial index goes with it and is recreated).
BEGIN;
ALTER TABLE games.games DROP COLUMN blowout_flag;
ALTER TABLE games.games ADD COLUMN blowout_flag BOOLEAN GENERATED ALWAYS AS (
    CASE
        WHEN home_score IS NOT NULL AND away_score IS NOT NULL
            THEN abs(home_score - away_score) > 20
        ELSE NULL
    END
) STORED;
CREATE INDEX idx_games_blowout ON games.games (blowout_flag) WHERE blowout_flag = TRUE;
COMMIT;
