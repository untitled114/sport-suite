# SQL in this repository

PostgreSQL does a large share of the work here: it stores the data, enforces its rules,
and answers the analytical questions the features are built from. This page shows where
the SQL is and what each piece shows. Every file named below is in this repository.

## Where it lives

| Kind | Files | Lines | Location |
|---|---|---|---|
| Named queries | 37 | 971 | `nba/sql/`, `mlb/sql/` |
| Schema definitions | 14 | 1,056 | `docker/sportsuite_db/`, `mlb/db/` |
| Migrations | 13 | 469 | `scripts/ops/migrations/` |
| dbt models | 2 | 231 | `dbt/models/` |

## How the code uses SQL

- **One query, one file.** The main analytical queries are not strings buried in Python. Each
  is a `.sql` file that opens with a comment header: what the query answers, its parameters in
  order, and the function that runs it. The code loads it by name:

  ```python
  from nba.sql import named_query

  cur.execute(named_query("team_season_pace_and_shooting"), (season,))
  ```

- **Values are bound parameters.** The query files take `%s` or named placeholders; no value
  is formatted into their text.
- **The files are tested.** `tests/unit/test_named_queries.py` fails if a query file has no
  header, holds more than one statement, is not run by the code its header names, or if the
  code asks for a name that does not exist.
  `tests/unit/test_named_queries_plan.py` goes further when a database is reachable: it has
  PostgreSQL plan every query (`EXPLAIN`), which catches a wrong column or an invalid
  statement without reading or writing a row.
  CI builds a database from the schema files (`docker-compose.yml`) and runs that test on
  every push, so the queries and the schema cannot drift apart unnoticed.
- **Writes are repeatable.** The `*_upsert` queries write with `ON CONFLICT`, so re-running a
  day replaces its rows.
- **Features never see their own game.** The per-player queries in `mlb/sql/` read games
  strictly before the target date.
- **Schema changes are scripts.** A migration that rewrites data runs in a transaction, and
  most end with a block that counts what is still wrong and aborts if anything is.

## Techniques, and where to see them

| Technique | What it is used for | Examples |
|---|---|---|
| Common table expressions | a query built in named steps | [`dbt/models/intermediate/int_rolling_stats.sql`](../dbt/models/intermediate/int_rolling_stats.sql), [`dbt/models/staging/stg_player_game_logs.sql`](../dbt/models/staging/stg_player_game_logs.sql), [`mlb/sql/opponent_runs_allowed_vs_league.sql`](../mlb/sql/opponent_runs_allowed_vs_league.sql) and 10 more |
| Window functions | ranking and rolling values inside a group | [`dbt/models/intermediate/int_rolling_stats.sql`](../dbt/models/intermediate/int_rolling_stats.sql), [`dbt/models/staging/stg_player_game_logs.sql`](../dbt/models/staging/stg_player_game_logs.sql), [`mlb/sql/player_rolling_stat.sql`](../mlb/sql/player_rolling_stat.sql) and 2 more |
| `DISTINCT ON` | keeping exactly one row per group, chosen by an order | [`nba/sql/gamelog_duplicates_count.sql`](../nba/sql/gamelog_duplicates_count.sql), [`nba/sql/gamelog_duplicates_delete.sql`](../nba/sql/gamelog_duplicates_delete.sql) |
| `LATERAL` subqueries | a subquery evaluated once per outer row | [`mlb/sql/batter_games_with_opposing_hand.sql`](../mlb/sql/batter_games_with_opposing_hand.sql), [`mlb/sql/lineup_games_with_faced_hand.sql`](../mlb/sql/lineup_games_with_faced_hand.sql) |
| `UNION ALL` to unpivot | home and away columns turned into one row per team | [`mlb/sql/team_rolling_runs.sql`](../mlb/sql/team_rolling_runs.sql), [`mlb/sql/team_season_runs.sql`](../mlb/sql/team_season_runs.sql), [`nba/sql/team_season_record.sql`](../nba/sql/team_season_record.sql) |
| Self-joins | a team's row paired with its opponent's row | [`nba/sql/team_games_with_opponent_points.sql`](../nba/sql/team_games_with_opponent_points.sql), [`nba/sql/team_recent_pace_and_margin.sql`](../nba/sql/team_recent_pace_and_margin.sql), [`nba/sql/team_season_pace_and_shooting.sql`](../nba/sql/team_season_pace_and_shooting.sql) and 2 more |
| Aggregates with `FILTER` | several conditional totals in one pass | [`mlb/sql/player_rolling_stat.sql`](../mlb/sql/player_rolling_stat.sql), [`mlb/sql/team_season_runs.sql`](../mlb/sql/team_season_runs.sql), [`nba/sql/team_season_record.sql`](../nba/sql/team_season_record.sql) and 2 more |
| `HAVING` | conditions on a group, such as duplicates and thresholds | [`mlb/sql/opponent_strikeout_rate_vs_league.sql`](../mlb/sql/opponent_strikeout_rate_vs_league.sql), [`nba/sql/gamelog_duplicates_count.sql`](../nba/sql/gamelog_duplicates_count.sql), [`nba/sql/gamelog_duplicates_delete.sql`](../nba/sql/gamelog_duplicates_delete.sql) and 3 more |
| `IS DISTINCT FROM` | comparisons that treat NULL as a value | [`nba/sql/box_score_team_points_mismatch.sql`](../nba/sql/box_score_team_points_mismatch.sql), [`scripts/ops/migrations/2026-11-10_games_season_end_year.sql`](../scripts/ops/migrations/2026-11-10_games_season_end_year.sql), [`scripts/ops/migrations/2026-11-10_player_game_logs_season_end_year.sql`](../scripts/ops/migrations/2026-11-10_player_game_logs_season_end_year.sql) and 2 more |
| Division guards | rates that return NULL instead of failing | [`mlb/sql/matchup_history_rebuild.sql`](../mlb/sql/matchup_history_rebuild.sql), [`mlb/sql/opponent_strikeout_rate_vs_league.sql`](../mlb/sql/opponent_strikeout_rate_vs_league.sql), [`nba/sql/team_season_pace_and_shooting.sql`](../nba/sql/team_season_pace_and_shooting.sql) |
| `ON CONFLICT` upserts | writes that are safe to run twice | [`mlb/sql/computed_features_upsert.sql`](../mlb/sql/computed_features_upsert.sql), [`mlb/sql/feature_metadata_upsert.sql`](../mlb/sql/feature_metadata_upsert.sql), [`nba/sql/computed_features_upsert.sql`](../nba/sql/computed_features_upsert.sql) and 6 more |
| `unaccent()` | matching names that carry diacritics | [`docker/sportsuite_db/03_players.sql`](../docker/sportsuite_db/03_players.sql), [`mlb/sql/batter_games_with_opposing_hand.sql`](../mlb/sql/batter_games_with_opposing_hand.sql), [`mlb/sql/batter_last_30_games.sql`](../mlb/sql/batter_last_30_games.sql) and 7 more |
| Arrays | array parameters and aggregated arrays | [`mlb/sql/home_park_batting_per_game.sql`](../mlb/sql/home_park_batting_per_game.sql), [`mlb/sql/team_rolling_runs.sql`](../mlb/sql/team_rolling_runs.sql), [`nba/sql/gamelog_duplicates_worst.sql`](../nba/sql/gamelog_duplicates_worst.sql) |
| Hypertables | time-partitioned tables (TimescaleDB) | [`docker/sportsuite_db/03_players.sql`](../docker/sportsuite_db/03_players.sql), [`docker/sportsuite_db/04_games.sql`](../docker/sportsuite_db/04_games.sql) |
| Generated columns | values the database derives and keeps in step | [`docker/sportsuite_db/04_games.sql`](../docker/sportsuite_db/04_games.sql), [`scripts/ops/migrations/2026-11-10_games_blowout_flag_null.sql`](../scripts/ops/migrations/2026-11-10_games_blowout_flag_null.sql) |
| `CHECK` constraints | rules the database enforces on every row | [`docker/sportsuite_db/04_games.sql`](../docker/sportsuite_db/04_games.sql), [`scripts/ops/migrations/2026-11-10_games_season_end_year.sql`](../scripts/ops/migrations/2026-11-10_games_season_end_year.sql), [`scripts/ops/migrations/2026-11-10_games_season_type.sql`](../scripts/ops/migrations/2026-11-10_games_season_type.sql) |
| Foreign keys | links between tables the database enforces | [`docker/sportsuite_db/03_players.sql`](../docker/sportsuite_db/03_players.sql), [`docker/sportsuite_db/04_games.sql`](../docker/sportsuite_db/04_games.sql), [`docker/sportsuite_db/08_features.sql`](../docker/sportsuite_db/08_features.sql) |
| Partial indexes | an index over only the rows that need it | [`docker/sportsuite_db/04_games.sql`](../docker/sportsuite_db/04_games.sql), [`docker/sportsuite_db/08_features.sql`](../docker/sportsuite_db/08_features.sql), [`scripts/ops/migrations/2026-11-10_games_blowout_flag_null.sql`](../scripts/ops/migrations/2026-11-10_games_blowout_flag_null.sql) |
| Functions | logic kept in the database | [`docker/sportsuite_db/03_players.sql`](../docker/sportsuite_db/03_players.sql) |
| Self-checking migrations | a data fix that verifies itself and aborts if wrong | [`scripts/ops/migrations/2026-11-10_games_neutral_site_away_teams.sql`](../scripts/ops/migrations/2026-11-10_games_neutral_site_away_teams.sql), [`scripts/ops/migrations/2026-11-10_games_season_end_year.sql`](../scripts/ops/migrations/2026-11-10_games_season_end_year.sql), [`scripts/ops/migrations/2026-11-10_player_game_logs_phantom_rows.sql`](../scripts/ops/migrations/2026-11-10_player_game_logs_phantom_rows.sql) and 5 more |
| Transactions | a data fix applied whole or not at all | [`scripts/ops/migrations/2026-11-10_games_blowout_flag_null.sql`](../scripts/ops/migrations/2026-11-10_games_blowout_flag_null.sql), [`scripts/ops/migrations/2026-11-10_games_neutral_site_away_teams.sql`](../scripts/ops/migrations/2026-11-10_games_neutral_site_away_teams.sql), [`scripts/ops/migrations/2026-11-10_games_season_end_year.sql`](../scripts/ops/migrations/2026-11-10_games_season_end_year.sql) and 8 more |
| dbt models | transformations as versioned SQL, with data tests beside them | [`dbt/models/intermediate/int_rolling_stats.sql`](../dbt/models/intermediate/int_rolling_stats.sql), [`dbt/models/staging/stg_player_game_logs.sql`](../dbt/models/staging/stg_player_game_logs.sql) |

## The named queries

### NBA (`nba/sql/`)

| Query | What it answers |
|---|---|
| [`nba/sql/box_score_team_points_mismatch.sql`](../nba/sql/box_score_team_points_mismatch.sql) | Team-games whose player box scores do not add up to the team's points. |
| [`nba/sql/computed_features_upsert.sql`](../nba/sql/computed_features_upsert.sql) | Store one feature vector (insert, or replace on re-run). |
| [`nba/sql/feature_metadata_upsert.sql`](../nba/sql/feature_metadata_upsert.sql) | Store a feature's training statistics and importance (insert, or replace on re-run). |
| [`nba/sql/feature_set_upsert.sql`](../nba/sql/feature_set_upsert.sql) | Register a feature set: its name, version, and the ordered list of features it holds. |
| [`nba/sql/gamelog_duplicates_count.sql`](../nba/sql/gamelog_duplicates_count.sql) | How many duplicate game-log rows the clean-up would delete (dry-run count). |
| [`nba/sql/gamelog_duplicates_delete.sql`](../nba/sql/gamelog_duplicates_delete.sql) | Delete duplicate game-log rows, keeping the best row of each (player, date). |
| [`nba/sql/gamelog_duplicates_worst.sql`](../nba/sql/gamelog_duplicates_worst.sql) | The ten player-days with the most duplicate game-log rows. |
| [`nba/sql/minutes_projections_upsert.sql`](../nba/sql/minutes_projections_upsert.sql) | Store the projected minutes per player (insert, or replace on re-run). |
| [`nba/sql/player_games_vs_opponent.sql`](../nba/sql/player_games_vs_opponent.sql) | Every game a player has played against one opponent before a date, newest first. |
| [`nba/sql/player_last_10_games_played.sql`](../nba/sql/player_last_10_games_played.sql) | A player's last 10 games with minutes played before a date, newest first. |
| [`nba/sql/player_profile_upsert.sql`](../nba/sql/player_profile_upsert.sql) | Add new players and refresh the team of known ones, without ever renaming a player. |
| [`nba/sql/player_recent_teams.sql`](../nba/sql/player_recent_teams.sql) | The team a player appeared for in each of his last 30 games before a date, newest first. |
| [`nba/sql/players_with_game_counts.sql`](../nba/sql/players_with_game_counts.sql) | Every player profile with how many game logs it has. |
| [`nba/sql/players_with_two_seasons_of_minutes.sql`](../nba/sql/players_with_two_seasons_of_minutes.sql) | Each rostered player with last season's and this season's minutes side by side. |
| [`nba/sql/team_box_from_player_box.sql`](../nba/sql/team_box_from_player_box.sql) | A team's shooting, rebounds, assists and turnovers for one game, summed from its players. |
| [`nba/sql/team_games_with_opponent_points.sql`](../nba/sql/team_games_with_opponent_points.sql) | Every finished team-game with the points the team scored and allowed. |
| [`nba/sql/team_recent_pace_and_margin.sql`](../nba/sql/team_recent_pace_and_margin.sql) | A team's average pace and score margin over its last 10 games before a date. |
| [`nba/sql/team_regulars_in_window.sql`](../nba/sql/team_regulars_in_window.sql) | A team's regulars: players who appeared in at least N of its games in a date window. |
| [`nba/sql/team_rolling_stats_upsert.sql`](../nba/sql/team_rolling_stats_upsert.sql) | Store one team's rolling-window stats as of a date (insert, or replace on re-run). |
| [`nba/sql/team_season_pace_and_shooting.sql`](../nba/sql/team_season_pace_and_shooting.sql) | Pace, true shooting and rebound share per team for one regular season. |
| [`nba/sql/team_season_record.sql`](../nba/sql/team_season_record.sql) | Games played, wins and losses per team for one regular season, from final scores. |

### MLB (`mlb/sql/`)

| Query | What it answers |
|---|---|
| [`mlb/sql/batter_games_with_opposing_hand.sql`](../mlb/sql/batter_games_with_opposing_hand.sql) | A batter's recent games, each tagged with the throwing hand of the pitcher he mostly faced. |
| [`mlb/sql/batter_last_30_games.sql`](../mlb/sql/batter_last_30_games.sql) | A batter's last 30 game logs before a date, newest first. |
| [`mlb/sql/batter_recent_totals.sql`](../mlb/sql/batter_recent_totals.sql) | A batter's totals over his last N games before a date. |
| [`mlb/sql/computed_features_upsert.sql`](../mlb/sql/computed_features_upsert.sql) | Store one feature vector (insert, or replace on re-run). |
| [`mlb/sql/feature_metadata_upsert.sql`](../mlb/sql/feature_metadata_upsert.sql) | Store a feature's training statistics and importance (insert, or replace on re-run). |
| [`mlb/sql/home_park_batting_per_game.sql`](../mlb/sql/home_park_batting_per_game.sql) | Home runs and hits per finished game, labelled with the home team's park. |
| [`mlb/sql/lineup_games_with_faced_hand.sql`](../mlb/sql/lineup_games_with_faced_hand.sql) | A team's batting lines over the last 60 days, each tagged with the hand of the pitcher faced. |
| [`mlb/sql/matchup_history_rebuild.sql`](../mlb/sql/matchup_history_rebuild.sql) | Fill the batter-versus-pitcher history table from the per-game encounters. |
| [`mlb/sql/opponent_runs_allowed_vs_league.sql`](../mlb/sql/opponent_runs_allowed_vs_league.sql) | A team's runs allowed per game beside the league average, for the latest season. |
| [`mlb/sql/opponent_strikeout_rate_vs_league.sql`](../mlb/sql/opponent_strikeout_rate_vs_league.sql) | A lineup's strikeout rate beside the league average, for the latest season with games. |
| [`mlb/sql/pitcher_last_30_games.sql`](../mlb/sql/pitcher_last_30_games.sql) | A pitcher's last 30 game logs before a date, newest first. |
| [`mlb/sql/pitcher_recent_totals.sql`](../mlb/sql/pitcher_recent_totals.sql) | A pitcher's totals over his last N games before a date. |
| [`mlb/sql/plate_appearances_by_lineup_slot.sql`](../mlb/sql/plate_appearances_by_lineup_slot.sql) | Average plate appearances per batting-order slot over a range of seasons. |
| [`mlb/sql/player_rolling_stat.sql`](../mlb/sql/player_rolling_stat.sql) | A player's last-5, last-10 and last-20 game averages of one stat, its spread, and his team. |
| [`mlb/sql/team_rolling_runs.sql`](../mlb/sql/team_rolling_runs.sql) | Average runs scored and allowed over each team's last N games. |
| [`mlb/sql/team_season_runs.sql`](../mlb/sql/team_season_runs.sql) | Runs scored, runs allowed and wins per team for one season. |

## Schema and migrations

| File | What it holds |
|---|---|
| [`docker/sportsuite_db/01_init.sql`](../docker/sportsuite_db/01_init.sql) | The extensions the database needs: TimescaleDB, pg_stat_statements and unaccent. |
| [`docker/sportsuite_db/02_schemas.sql`](../docker/sportsuite_db/02_schemas.sql) | One schema per domain, and the application role's access to each. |
| [`docker/sportsuite_db/03_players.sql`](../docker/sportsuite_db/03_players.sql) | Player profiles, season stats, game logs (a hypertable with its two unique keys), rolling stats, minutes projections, and the `name_key` function that normalises a player name. |
| [`docker/sportsuite_db/04_games.sql`](../docker/sportsuite_db/04_games.sql) | Games and team game logs, with a generated blowout flag, a check on the season type, a unique key per date and pair of teams, and a partial index. |
| [`docker/sportsuite_db/05_teams.sql`](../docker/sportsuite_db/05_teams.sql) | Teams, team season stats and team rolling stats. |
| [`docker/sportsuite_db/06_intelligence.sql`](../docker/sportsuite_db/06_intelligence.sql) | The injury report: one row per player per day, with status and expected return. |
| [`docker/sportsuite_db/08_features.sql`](../docker/sportsuite_db/08_features.sql) | The feature store: named feature sets, computed feature vectors as JSON, and per-feature training statistics. |
| [`mlb/db/schema.sql`](../mlb/db/schema.sql) | The MLB tables: pitcher and batter profiles and game logs, games, probable pitchers, teams, the feature store and per-game Statcast metrics. |
| [`mlb/db/schema_expected_pa.sql`](../mlb/db/schema_expected_pa.sql) | Expected plate appearances by lineup slot, computed from the stored games. |
| [`mlb/db/schema_parkfactors.sql`](../mlb/db/schema_parkfactors.sql) | Park factors for runs, home runs and hits, computed from the stored games. |
| [`mlb/db/schema_pitchmix.sql`](../mlb/db/schema_pitchmix.sql) | Per-game pitch-mix and batted-ball metrics, batter-versus-pitcher encounters, and their rolled-up history. |
| [`mlb/db/schema_sabermetric.sql`](../mlb/db/schema_sabermetric.sql) | Home runs allowed per pitcher-game, the column FIP needs. |
| [`mlb/db/schema_volume.sql`](../mlb/db/schema_volume.sql) | Each batter's lineup slot per game. |
| [`mlb/db/schema_xbh.sql`](../mlb/db/schema_xbh.sql) | The extra-base-hit and walk components wOBA needs, per batter-game. |
| [`scripts/ops/migrations/2026-10-29_team_game_logs_unique.sql`](../scripts/ops/migrations/2026-10-29_team_game_logs_unique.sql) | Adds the unique key the team game log's upsert relies on. |
| [`scripts/ops/migrations/2026-11-10_games_blowout_flag_null.sql`](../scripts/ops/migrations/2026-11-10_games_blowout_flag_null.sql) | Makes the generated blowout flag NULL, not FALSE, for a game without both scores. |
| [`scripts/ops/migrations/2026-11-10_games_neutral_site_away_teams.sql`](../scripts/ops/migrations/2026-11-10_games_neutral_site_away_teams.sql) | Corrects five neutral-site games that were stored with the wrong home and away teams, and checks the result. |
| [`scripts/ops/migrations/2026-11-10_games_season_end_year.sql`](../scripts/ops/migrations/2026-11-10_games_season_end_year.sql) | Moves every game to the end-year season label and gives every game a season type, then verifies the counts. |
| [`scripts/ops/migrations/2026-11-10_games_season_type.sql`](../scripts/ops/migrations/2026-11-10_games_season_type.sql) | Adds the season type (regular season, play-in, playoffs) so records can be computed per part of the season. |
| [`scripts/ops/migrations/2026-11-10_games_start_time.sql`](../scripts/ops/migrations/2026-11-10_games_start_time.sql) | Adds each game's start time. |
| [`scripts/ops/migrations/2026-11-10_player_game_logs_phantom_rows.sql`](../scripts/ops/migrations/2026-11-10_player_game_logs_phantom_rows.sql) | Deletes the rows one bad backfill wrote, identified exactly, with a check that nothing else is touched. |
| [`scripts/ops/migrations/2026-11-10_player_game_logs_season_end_year.sql`](../scripts/ops/migrations/2026-11-10_player_game_logs_season_end_year.sql) | Moves the player game logs to the end-year season label, as a function of the game date. |
| [`scripts/ops/migrations/2026-11-10_player_profile_missing_players.sql`](../scripts/ops/migrations/2026-11-10_player_profile_missing_players.sql) | Adds players who appear in box scores but had no profile. |
| [`scripts/ops/migrations/2026-11-10_player_season_stats_end_year.sql`](../scripts/ops/migrations/2026-11-10_player_season_stats_end_year.sql) | Moves the player season stats to the end-year season label. |
| [`scripts/ops/migrations/2026-11-10_team_game_logs_season_end_year.sql`](../scripts/ops/migrations/2026-11-10_team_game_logs_season_end_year.sql) | Moves the team game logs to the end-year season label. |
| [`scripts/ops/migrations/2026-11-11_team_game_logs_box_from_players.sql`](../scripts/ops/migrations/2026-11-11_team_game_logs_box_from_players.sql) | Rebuilds team box-score columns from the players' rows where they do not add up. |
| [`scripts/ops/migrations/2026-11-11_team_game_logs_pace_ratings.sql`](../scripts/ops/migrations/2026-11-11_team_game_logs_pace_ratings.sql) | Recomputes per-game possessions, pace and ratings from the box score, replacing placeholder values. |
| [`dbt/models/intermediate/int_rolling_stats.sql`](../dbt/models/intermediate/int_rolling_stats.sql) | Rolling averages over the last 3, 5, 10 and 20 games per player, with momentum and consistency. |
| [`dbt/models/staging/stg_player_game_logs.sql`](../dbt/models/staging/stg_player_game_logs.sql) | A staging view over the player game logs: cleaned fields, shooting percentage and points per minute. |
