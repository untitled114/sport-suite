# sport-suite

A data platform for NBA and MLB player statistics: it loads games, box scores, rosters and
pitch-level data from public sources into PostgreSQL, builds features from them, projects
player stat lines, and keeps the reference data needed to check features for drift.

Much of the work is SQL. The schemas, the dated migrations, the dbt models and 37 named
analytical queries are all in this repository, and [docs/SQL.md](docs/SQL.md) is the guide to
them: what each file holds, which technique it shows, and which function runs it.

This repository is the public part of a larger system that runs in production. Some parts of
that system are private and are not described here, so a few schemas and tables that the code
reads have no definition in this repository.

## SQL

PostgreSQL does a large share of the work: it stores the data, enforces its rules, and answers
the questions the features are built from. The main analytical queries are files, not strings
inside Python (short lookups and single-row writes stay beside the code that runs them):

```python
from nba.sql import named_query

cur.execute(named_query("team_season_pace_and_shooting"), (season,))
```

[docs/SQL.md](docs/SQL.md) is a guide to all of it: where the SQL lives, how the code uses it,
which file shows which technique (window functions, `DISTINCT ON`, `LATERAL`, upserts,
hypertables, self-checking migrations and more), and a catalogue of every named query.

## What is here

| Area | Path | What it does |
|---|---|---|
| NBA loaders | `nba/scripts/` | Games, team and player box scores, rosters, injuries and season checks, with backfills for missing dates |
| MLB loaders | `mlb/scripts/` | Games, batter and pitcher game logs, lineups, handedness and Statcast pitch data, with backfills |
| Source clients | `nba/scripts/utilities/`, `mlb/scripts/utilities/` | Thin wrappers over the official stats clients, MLB Stats API and Statcast |
| Features | `nba/features/`, `mlb/features/` | One extractor per source of signal over a base class per sport, a feature schema for MLB and a feature store |
| Models | `nba/models/`, `mlb/models/` | A stat projection for MLB, a game-context adjustment for NBA, and MLflow experiment tracking. The NBA projection models themselves are part of the private system |
| Monitoring | `nba/core/` | Reference distributions of training features and a service that compares current features with them |
| Named queries | `nba/sql/`, `mlb/sql/` | The analytical queries the code runs, one documented `.sql` file each, loaded by name |
| Database | `docker/sportsuite_db/`, `mlb/db/`, `scripts/ops/migrations/` | Schema definitions for the NBA players, games, teams, injury-report and feature-store tables and for the MLB tables, dated migrations, and a compose file that builds a database from them |
| Transformations | `dbt/` | A staging model and an intermediate rolling-stats model over the player game logs, with data tests |
| Scheduling | `airflow/dags/` | A nightly backup job and the shared failure callbacks |
| Operations | `scripts/ops/` | The backup script the nightly job runs (dump, verify, prune) and backfills |
| Infrastructure | `terraform/aws/` | Network, compute, storage and access for a single-host deployment |
| Tests | `tests/unit/` | Unit tests for the loaders, feature extractors, tracking and helpers |

## Design notes

- **Reruns are safe.** Loaders check for or upsert on natural keys (a player and a game date, a
  game id), so a rerun or a backfill does not store a game twice.
- **Features look backwards.** Rolling features are computed from games before the one being
  described.
- **Queries are files.** Each main analytical query is a `.sql` file with a header saying what it
  answers, its parameters and the function that runs it; a test keeps the files and the code in
  step.
- **A name key in the database.** `players.name_key` normalises a player name (case, accents,
  punctuation, suffixes) so that feeds spelling a name differently can be matched to one player.
- **Credentials from the environment.** Database credentials are read from the environment and
  are never written into the code.
- **Recorded responses as fixtures.** The fixture folder holds recordings of real responses, with
  a note saying what was recorded and when; most unit tests use small inline inputs and mocks.

More detail is in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Running the checks

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
python -m pytest tests -q
```

The unit tests need no database and no network.

To check the SQL against a real database, start one from the schema files and have it plan every
named query:

```bash
DB_PASSWORD=choose-one docker compose up -d --wait
DB_PASSWORD=choose-one DB_USER=postgres python -m pytest tests/unit/test_named_queries_plan.py -q
```

CI runs both: the unit tests, and this check on a database built from the schema files.

## Related

[cephalon-fleet](https://github.com/untitled114/cephalon-fleet) holds the Discord services that
deliver this platform's data.

## Licence

MIT. See [LICENSE](LICENSE).
