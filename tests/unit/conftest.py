"""Unit-test hygiene for MLflow: no test writes the repo's mlruns.db or leaves a run open.

A tracker can start a real MLflow run and leave it open. Each session's store is a temp
directory, and each test ends whatever run it left open, so no test writes mlruns.db in the
working directory or trips over a run another test started.
"""

import os

import pytest


@pytest.fixture(autouse=True, scope="session")
def _mlflow_store_in_tmp(tmp_path_factory):
    previous = os.environ.get("MLFLOW_TRACKING_URI")
    store = tmp_path_factory.mktemp("mlflow") / "mlruns.db"
    os.environ["MLFLOW_TRACKING_URI"] = f"sqlite:///{store}"
    yield
    if previous is None:
        os.environ.pop("MLFLOW_TRACKING_URI", None)
    else:
        os.environ["MLFLOW_TRACKING_URI"] = previous


@pytest.fixture(autouse=True)
def _end_open_mlflow_runs():
    yield
    try:
        import mlflow
    except ImportError:
        return
    while mlflow.active_run() is not None:
        mlflow.end_run()
