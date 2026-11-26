"""Tests for mlb.models.mlflow_tracking - ModelTracker lifecycle.

The mlflow module is patched throughout; no real tracking server is touched.
Both the enabled (mlflow present) and disabled (no-op) code paths are covered.
"""

import subprocess
from unittest.mock import MagicMock, patch

import mlb.models.mlflow_tracking as mt
from mlb.models.mlflow_tracking import ModelTracker


def _enabled_tracker(mock_mlflow):
    """Build a tracker with mlflow patched + enabled, no active run at setup."""
    mock_mlflow.active_run.return_value = None
    mock_mlflow.get_experiment_by_name.return_value = MagicMock()
    return ModelTracker()


class TestHelpers:
    def test_git_hash_success(self):
        result = MagicMock(returncode=0, stdout="abc1234\n")
        with patch.object(subprocess, "run", return_value=result):
            assert mt._get_git_hash() == "abc1234"

    def test_git_hash_nonzero_returncode(self):
        result = MagicMock(returncode=1, stdout="")
        with patch.object(subprocess, "run", return_value=result):
            assert mt._get_git_hash() is None

    def test_git_hash_exception(self):
        with patch.object(subprocess, "run", side_effect=OSError("no git")):
            assert mt._get_git_hash() is None

    def test_package_version_success(self):
        # mlb.__version__ exists; just assert it returns a string.
        assert isinstance(mt._get_package_version(), str)

    def test_package_version_exception(self):
        with patch("builtins.__import__", side_effect=ImportError("boom")):
            assert mt._get_package_version() == "unknown"


class TestDisabledTracker:
    """When mlflow is unavailable, every method is a silent no-op."""

    def _disabled(self):
        with patch.object(mt, "MLFLOW_AVAILABLE", False):
            return ModelTracker()

    def test_no_setup_when_disabled(self):
        t = self._disabled()
        assert t.enabled is False
        assert t.run_id is None

    def test_start_run_returns_none(self):
        t = self._disabled()
        assert t.start_run("run1") is None

    def test_end_run_noop(self):
        t = self._disabled()
        assert t.end_run() is None

    def test_log_methods_noop(self):
        t = self._disabled()
        assert t.log_params({"a": 1}) is None
        assert t.log_metrics({"m": 1.0}) is None
        assert t.log_feature_importance(["f"], [1.0]) is None
        assert t.log_artifact("x") is None
        assert t.log_model_path("y") is None


class TestEnabledSetup:
    def test_setup_creates_experiment_when_missing(self):
        with patch.object(mt, "mlflow") as mlf, patch.object(mt, "MLFLOW_AVAILABLE", True):
            mlf.active_run.return_value = None
            mlf.get_experiment_by_name.return_value = None
            ModelTracker()
            mlf.create_experiment.assert_called_once_with(mt.DEFAULT_EXPERIMENT)
            mlf.set_experiment.assert_called_once()

    def test_setup_ends_orphaned_run(self):
        with patch.object(mt, "mlflow") as mlf, patch.object(mt, "MLFLOW_AVAILABLE", True):
            orphan = MagicMock()
            orphan.info.run_id = "orphan123"
            # active at setup, then not when checking experiment.
            mlf.active_run.return_value = orphan
            mlf.get_experiment_by_name.return_value = MagicMock()
            ModelTracker()
            mlf.end_run.assert_called()

    def test_explicit_tracking_uri(self):
        with patch.object(mt, "mlflow") as mlf, patch.object(mt, "MLFLOW_AVAILABLE", True):
            mlf.active_run.return_value = None
            mlf.get_experiment_by_name.return_value = MagicMock()
            ModelTracker(tracking_uri="sqlite:///custom.db")
            mlf.set_tracking_uri.assert_called_with("sqlite:///custom.db")


class TestRunLifecycle:
    def test_start_run_success(self):
        with patch.object(mt, "mlflow") as mlf, patch.object(mt, "MLFLOW_AVAILABLE", True):
            t = _enabled_tracker(mlf)
            run = MagicMock()
            run.info.run_id = "run-xyz"
            mlf.active_run.return_value = None
            mlf.start_run.return_value = run
            with patch.object(mt, "_get_git_hash", return_value="deadbee"):
                rid = t.start_run("myrun", tags={"extra": "v"})
            assert rid == "run-xyz"
            assert t.run_id == "run-xyz"
            tags = mlf.start_run.call_args[1]["tags"]
            assert tags["git_hash"] == "deadbee"
            assert tags["extra"] == "v"

    def test_start_run_no_git_hash(self):
        with patch.object(mt, "mlflow") as mlf, patch.object(mt, "MLFLOW_AVAILABLE", True):
            t = _enabled_tracker(mlf)
            run = MagicMock()
            run.info.run_id = "r2"
            mlf.active_run.return_value = None
            mlf.start_run.return_value = run
            with patch.object(mt, "_get_git_hash", return_value=None):
                rid = t.start_run("noGit")
            assert rid == "r2"
            assert "git_hash" not in mlf.start_run.call_args[1]["tags"]

    def test_start_run_ends_existing_active(self):
        with patch.object(mt, "mlflow") as mlf, patch.object(mt, "MLFLOW_AVAILABLE", True):
            t = _enabled_tracker(mlf)
            run = MagicMock()
            run.info.run_id = "r3"
            mlf.active_run.return_value = MagicMock()  # active when start_run called
            mlf.start_run.return_value = run
            t.start_run("again")
            mlf.end_run.assert_called()

    def test_start_run_exception(self):
        with patch.object(mt, "mlflow") as mlf, patch.object(mt, "MLFLOW_AVAILABLE", True):
            t = _enabled_tracker(mlf)
            mlf.active_run.return_value = None
            mlf.start_run.side_effect = RuntimeError("fail")
            assert t.start_run("boom") is None
            assert t.run_id is None

    def test_end_run_success(self):
        with patch.object(mt, "mlflow") as mlf, patch.object(mt, "MLFLOW_AVAILABLE", True):
            t = _enabled_tracker(mlf)
            t._active = True
            t.end_run()
            mlf.end_run.assert_called()
            assert t._active is False

    def test_end_run_exception(self):
        with patch.object(mt, "mlflow") as mlf, patch.object(mt, "MLFLOW_AVAILABLE", True):
            t = _enabled_tracker(mlf)
            t._active = True
            mlf.end_run.side_effect = RuntimeError("x")
            t.end_run()
            assert t._active is False

    def test_end_run_not_active(self):
        with patch.object(mt, "mlflow") as mlf, patch.object(mt, "MLFLOW_AVAILABLE", True):
            t = _enabled_tracker(mlf)
            t._active = False
            mlf.reset_mock()
            t.end_run()
            mlf.end_run.assert_not_called()


class TestLogging:
    def _active(self, mlf):
        t = _enabled_tracker(mlf)
        t._active = True
        return t

    def test_log_params_mixed_types(self):
        with patch.object(mt, "mlflow") as mlf, patch.object(mt, "MLFLOW_AVAILABLE", True):
            t = self._active(mlf)
            t.log_params({"s": "x", "i": 1, "f": 1.5, "b": True, "obj": [1, 2]})
            clean = mlf.log_params.call_args[0][0]
            assert clean["s"] == "x" and clean["i"] == 1
            assert clean["obj"] == "[1, 2]"  # stringified

    def test_log_params_exception(self):
        with patch.object(mt, "mlflow") as mlf, patch.object(mt, "MLFLOW_AVAILABLE", True):
            t = self._active(mlf)
            mlf.log_params.side_effect = RuntimeError("e")
            assert t.log_params({"a": 1}) is None

    def test_log_params_not_active(self):
        with patch.object(mt, "mlflow") as mlf, patch.object(mt, "MLFLOW_AVAILABLE", True):
            t = _enabled_tracker(mlf)
            t._active = False
            t.log_params({"a": 1})
            mlf.log_params.assert_not_called()

    def test_log_metrics_skips_non_numeric(self):
        with patch.object(mt, "mlflow") as mlf, patch.object(mt, "MLFLOW_AVAILABLE", True):
            t = self._active(mlf)
            t.log_metrics({"good": 1.5, "bad": "nope"})
            logged = [c[0][0] for c in mlf.log_metric.call_args_list]
            assert "good" in logged and "bad" not in logged

    def test_log_metrics_exception(self):
        with patch.object(mt, "mlflow") as mlf, patch.object(mt, "MLFLOW_AVAILABLE", True):
            t = self._active(mlf)
            mlf.log_metric.side_effect = RuntimeError("e")
            assert t.log_metrics({"m": 1.0}) is None

    def test_log_metrics_not_active(self):
        with patch.object(mt, "mlflow") as mlf, patch.object(mt, "MLFLOW_AVAILABLE", True):
            t = _enabled_tracker(mlf)
            t._active = False
            t.log_metrics({"m": 1.0})
            mlf.log_metric.assert_not_called()

    def test_log_feature_importance_success(self):
        with patch.object(mt, "mlflow") as mlf, patch.object(mt, "MLFLOW_AVAILABLE", True):
            t = self._active(mlf)
            t.log_feature_importance(["a", "b", "c"], [0.1, 0.9, 0.5], head="reg", top_n=2)
            # top 2 by importance: b (0.9), c (0.5)
            tagged = [c[0][1] for c in mlf.set_tag.call_args_list]
            assert "b" in tagged and "c" in tagged
            mlf.log_artifact.assert_called_once()

    def test_log_feature_importance_artifact_exception(self):
        with patch.object(mt, "mlflow") as mlf, patch.object(mt, "MLFLOW_AVAILABLE", True):
            t = self._active(mlf)
            mlf.log_artifact.side_effect = RuntimeError("e")
            assert t.log_feature_importance(["a"], [1.0]) is None

    def test_log_feature_importance_not_active(self):
        with patch.object(mt, "mlflow") as mlf, patch.object(mt, "MLFLOW_AVAILABLE", True):
            t = _enabled_tracker(mlf)
            t._active = False
            t.log_feature_importance(["a"], [1.0])
            mlf.set_tag.assert_not_called()

    def test_log_artifact_success_and_exception(self):
        with patch.object(mt, "mlflow") as mlf, patch.object(mt, "MLFLOW_AVAILABLE", True):
            t = self._active(mlf)
            t.log_artifact("/tmp/x.txt", "sub")
            mlf.log_artifact.assert_called_with("/tmp/x.txt", "sub")
            mlf.log_artifact.side_effect = RuntimeError("e")
            t.log_artifact("/tmp/y.txt")  # swallowed

    def test_log_artifact_not_active(self):
        with patch.object(mt, "mlflow") as mlf, patch.object(mt, "MLFLOW_AVAILABLE", True):
            t = _enabled_tracker(mlf)
            t._active = False
            t.log_artifact("/tmp/x.txt")
            mlf.log_artifact.assert_not_called()

    def test_log_model_path_success_and_exception(self):
        with patch.object(mt, "mlflow") as mlf, patch.object(mt, "MLFLOW_AVAILABLE", True):
            t = self._active(mlf)
            t.log_model_path("model.pkl")
            mlf.set_tag.assert_called_with("model_pkl_path", "model.pkl")
            mlf.set_tag.side_effect = RuntimeError("e")
            t.log_model_path("model2.pkl")  # swallowed

    def test_log_model_path_not_active(self):
        with patch.object(mt, "mlflow") as mlf, patch.object(mt, "MLFLOW_AVAILABLE", True):
            t = _enabled_tracker(mlf)
            t._active = False
            t.log_model_path("model.pkl")
            mlf.set_tag.assert_not_called()
