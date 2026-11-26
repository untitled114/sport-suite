# Architecture

## Flow

```
public sources            loaders                 database              features and models
(official stats     ->    nba/scripts       ->    PostgreSQL      ->    nba/features, mlb/features
 clients, MLB Stats       mlb/scripts             (one instance,        nba/models,   mlb/models
 API, Statcast, ESPN)                             one schema per
                                                  domain)                     |
                                                       |                      v
                                                  dbt models            drift monitoring
                                                  (dbt/)                (nba/core)
```

## Database

One PostgreSQL instance holds every domain in its own schema (players, games, teams and so on),
for each sport. Code asks `nba.config.database` or `mlb.config.database` for a schema's connection
settings, which sets the search path.

Schema definitions for the NBA players, games, teams, intelligence (the injury report) and
feature-store schemas are in `docker/sportsuite_db/`, and the MLB tables are in `mlb/db/`.
`docker-compose.yml` starts a database and loads them all; the file numbers are load order,
and a gap is a file that is not part of this repository. Other schemas are part of the
private system.

The schema files describe the current state: a new database is built from them alone. The
dated SQL files in `scripts/ops/migrations/` are the record of how an existing database was
brought to that state. Each one states what it changes and why. They are not replayed on a
new database, and files sharing a date were applied in dependency order, not name order (a
column is added before the migration that fills it).

The analytical queries the code runs are files in `nba/sql/` and `mlb/sql/`, loaded by name.
[SQL.md](SQL.md) lists them and shows which file uses which technique.

## Loading

Loaders are small command-line programs.

- They check for or upsert on natural keys (a player and a game date, a game id), so reruns are
  safe.
- The game and game-log loaders load recent dates by default and take a date range or a season
  for backfills.
- They skip what does not belong: for example, only games between two league teams are stored.

Team abbreviations and player names differ between feeds. `nba/utils/team_utils.py`,
`mlb/utils/team_abbrev.py` and the name normalisers map each spelling to one canonical value.

## Features

Each extractor (`nba/features/extractors/`, `mlb/features/extractors/`) reads one kind of signal
and returns a fixed set of named values with defaults. All of them derive from a base class that
supplies safe numeric conversion and validation. Rolling features are computed from games before
the one being described.

`mlb/features/feature_schema.py` states which feature groups apply to pitchers and which to
batters. The feature stores read and write computed feature sets so that training and inference
can use the same values.

## Models and tracking

`mlb/models/projection_model.py` projects a player's stat line from park-adjusted rolling
averages and opponent strength. `nba/models/context_modifier.py` adjusts a projection for game
context. Experiments are logged through `mlflow_tracking.py`, which records parameters, metrics
and artifacts for every run and does nothing when MLflow is not installed.

## Monitoring

`nba/core/reference_distributions.py` builds and stores the distributions of the features a model
was trained on. `nba/core/drift_service.py` compares current feature values with those references
and reports features that have moved. `nba/core/cli_drift_check.py` builds the references and
reports their status from the command line.

## Operations

`airflow/dags/ops_db_backup.py` runs `scripts/ops/backup_databases.sh` every night. The script
dumps each configured database, checks that each dump can be read back, and removes dumps older
than the retention period; a failed backup fails the task, and `airflow/dags/callbacks.py` logs
the failure and sends a notification when a notifier is installed. `terraform/aws/` describes
a single-host deployment: network, compute, storage and access.
