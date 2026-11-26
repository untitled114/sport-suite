-- games.games.start_time: each game's tip time.
--
-- No table held a tip time. fetch_daily_stats now fills start_time from ESPN's scoreboard, and
-- scripts/ops/backfill_games_start_time.py fills the past. NULL means unknown; no default.
-- An added nullable column changes no existing read of games.games.

BEGIN;

ALTER TABLE games.games ADD COLUMN IF NOT EXISTS start_time timestamptz;

COMMIT;
