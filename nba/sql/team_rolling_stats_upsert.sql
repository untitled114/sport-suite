-- Store one team's rolling-window stats as of a date (insert, or replace on re-run).
--
-- The key is (team, as-of date, window size), so re-running a day replaces its rows and the
-- loader is safe to repeat.
--
-- Parameters, in order:
--   1. one value per column, in the column order of the INSERT
-- Run by: nba/scripts/loaders/compute_team_rolling_stats.py (upsert_rolling_stats)
INSERT INTO team_rolling_stats (
    team_abbrev, as_of_date, window_size, games_in_window,
    avg_points, avg_points_allowed, avg_pace,
    avg_offensive_rating, avg_defensive_rating, net_rating,
    wins_in_window, losses_in_window, win_pct
) VALUES %s
ON CONFLICT (team_abbrev, as_of_date, window_size) DO UPDATE SET
    games_in_window = EXCLUDED.games_in_window,
    avg_points = EXCLUDED.avg_points,
    avg_points_allowed = EXCLUDED.avg_points_allowed,
    avg_pace = EXCLUDED.avg_pace,
    avg_offensive_rating = EXCLUDED.avg_offensive_rating,
    avg_defensive_rating = EXCLUDED.avg_defensive_rating,
    net_rating = EXCLUDED.net_rating,
    wins_in_window = EXCLUDED.wins_in_window,
    losses_in_window = EXCLUDED.losses_in_window,
    win_pct = EXCLUDED.win_pct;
