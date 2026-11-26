"""
Ops - Nightly Database Backup DAG

Scheduled: Daily at 3:00 AM Eastern, a quiet window before the morning jobs.
Purpose: Run scripts/ops/backup_databases.sh, which pg_dumps each configured database
to the backup directory, verifies each dump with pg_restore --list, and prunes dumps older than 30 days.
A non-zero exit fails the task, so a missed backup reaches the operator through the
standard failure callback.

Restore procedure is documented at the top of the script.
"""

from __future__ import annotations

import subprocess
from datetime import datetime, timedelta
from pathlib import Path

from callbacks import on_failure, on_retry, on_success

from airflow.decorators import dag, task
from airflow.timetables.trigger import CronTriggerTimetable

PROJECT_ROOT = str(Path(__file__).resolve().parent.parent.parent)
BACKUP_SCRIPT = f"{PROJECT_ROOT}/scripts/ops/backup_databases.sh"

default_args = {
    "owner": "ops",
    "depends_on_past": False,
    "retries": 1,
    "retry_delay": timedelta(minutes=15),
    "execution_timeout": timedelta(minutes=30),
    "on_success_callback": on_success,
    "on_failure_callback": on_failure,
    "on_retry_callback": on_retry,
}


@dag(
    dag_id="ops_db_backup",
    description="Nightly pg_dump of production databases - 3 AM EST, 30-day retention",
    schedule=CronTriggerTimetable("0 3 * * *", timezone="America/New_York"),
    start_date=datetime(2026, 9, 23),
    catchup=False,
    tags=["ops", "backup"],
    default_args=default_args,
    max_active_runs=1,
    doc_md=__doc__,
)
def ops_db_backup():
    """Nightly logical backup of every production database."""

    @task(task_id="backup_databases")
    def backup_databases() -> str:
        """Run the backup script; raise on failure so the failure callback alerts."""
        result = subprocess.run(
            ["bash", BACKUP_SCRIPT], capture_output=True, text=True, timeout=1800
        )
        print(result.stdout)
        if result.returncode != 0:
            raise RuntimeError(f"backup failed (exit {result.returncode}): {result.stdout[-1500:]}")
        return result.stdout.strip().splitlines()[-1] if result.stdout.strip() else "ok"

    backup_databases()


ops_db_backup()
