-- Each rostered player with last season's and this season's minutes side by side.
--
-- Two LEFT JOINs to the same season table, one per season, so a rookie (no previous season)
-- and a player who has not played yet this season both still appear. Seasons are stored under
-- their END year (2027 is 2026-27). Age and experience are estimated from the draft year,
-- counted from the season's start year (end year minus 1), assuming a player is drafted at 19.
--
-- Parameters (named):
--   curr: the current season's end year
--   prev: the previous season's end year
-- Run by: nba/scripts/loaders/calculate_minutes_projections.py (load_player_data)
SELECT
    pp.player_id,
    pp.full_name,
    pp.team_abbrev,
    pp.position,
    pp.draft_year,
    pss_prev.minutes_per_game AS prev_mpg,
    pss_prev.games_played AS prev_gp,
    pss_prev.season AS prev_season,
    pss_curr.minutes_per_game AS curr_mpg,
    pss_curr.games_played AS curr_gp,
    CASE
        WHEN pp.draft_year IS NOT NULL THEN %(curr)s - 1 - pp.draft_year + 19
    END AS estimated_age,
    CASE
        WHEN pp.draft_year IS NOT NULL THEN %(curr)s - 1 - pp.draft_year
    END AS years_experience
FROM player_profile pp
LEFT JOIN player_season_stats pss_prev
    ON pp.player_id = pss_prev.player_id
    AND pss_prev.season = %(prev)s
LEFT JOIN player_season_stats pss_curr
    ON pp.player_id = pss_curr.player_id
    AND pss_curr.season = %(curr)s
WHERE pp.team_abbrev IS NOT NULL;
