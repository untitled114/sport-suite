-- A team's shooting, rebounds, assists and turnovers for one game, summed from its players.
--
-- Fills team box-score columns the team feed did not supply from the player rows of the same
-- game.
--
-- Parameters, in order:
--   1. team abbreviation (as recorded on the player's game log, so a traded player counts
--      for the team he played for that day)
--   2. game date
-- A team plays once a day, so team and date identify the game. The game id is not used: the
-- player logs carry the league's id for some seasons and the daily loader's own id
-- ("<date>_<team>") for others.
-- Run by: nba/scripts/loaders/aggregate_team_shooting_from_players.py
--         (aggregate_team_shooting_stats)
SELECT
    SUM(fg_made) as fg_made,
    SUM(fg_attempted) as fg_attempted,
    SUM(three_pointers_made) as three_pt_made,
    SUM(three_pt_attempted) as three_pt_attempted,
    SUM(ft_made) as ft_made,
    SUM(ft_attempted) as ft_attempted,
    SUM(rebounds) as rebounds,
    SUM(assists) as assists,
    SUM(turnovers) as turnovers
FROM player_game_logs
WHERE team_abbrev = %s
  AND game_date = %s;
