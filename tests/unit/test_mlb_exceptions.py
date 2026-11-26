"""Unit tests for mlb.core.exceptions.

Exercises every exception constructor branch (with and without optional context)
and the class hierarchy.
"""

import pytest

from mlb.core.exceptions import (
    DataValidationError,
    MLBError,
    ModelError,
    ModelLoadError,
    ModelNotFoundError,
    ModelPredictionError,
    PickleLoadError,
    SerializationError,
)

# ---------------------------------------------------------------------------
# Hierarchy
# ---------------------------------------------------------------------------


def test_hierarchy():
    assert issubclass(ModelError, MLBError)
    assert issubclass(ModelNotFoundError, ModelError)
    assert issubclass(ModelLoadError, ModelError)
    assert issubclass(ModelPredictionError, ModelError)
    assert issubclass(DataValidationError, MLBError)
    assert issubclass(SerializationError, MLBError)
    assert issubclass(PickleLoadError, SerializationError)


def test_base_is_exception():
    assert issubclass(MLBError, Exception)


# ---------------------------------------------------------------------------
# ModelNotFoundError
# ---------------------------------------------------------------------------


def test_model_not_found_without_stat_type():
    err = ModelNotFoundError("/path/model.pkl")
    assert err.model_path == "/path/model.pkl"
    assert err.stat_type is None
    assert str(err) == "Model not found: /path/model.pkl"


def test_model_not_found_with_stat_type():
    err = ModelNotFoundError("/path/model.pkl", stat_type="STRIKEOUTS")
    assert err.stat_type == "STRIKEOUTS"
    assert str(err) == "STRIKEOUTS model not found: /path/model.pkl"


# ---------------------------------------------------------------------------
# ModelLoadError
# ---------------------------------------------------------------------------


def test_model_load_without_reason():
    err = ModelLoadError("/path/model.pkl")
    assert err.reason is None
    assert str(err) == "Failed to load model: /path/model.pkl"


def test_model_load_with_reason():
    err = ModelLoadError("/path/model.pkl", reason="version mismatch")
    assert str(err) == "Failed to load model: /path/model.pkl - version mismatch"


# ---------------------------------------------------------------------------
# ModelPredictionError
# ---------------------------------------------------------------------------


def test_model_prediction_plain_message():
    err = ModelPredictionError("boom")
    assert err.player_name is None
    assert err.stat_type is None
    assert str(err) == "boom"


def test_model_prediction_with_context():
    err = ModelPredictionError("boom", player_name="Gerrit Cole", stat_type="STRIKEOUTS")
    assert err.player_name == "Gerrit Cole"
    assert err.stat_type == "STRIKEOUTS"
    assert str(err) == "boom (player=Gerrit Cole, stat=STRIKEOUTS)"


def test_model_prediction_partial_context_no_suffix():
    # Only one of the two provided -> message stays plain (both required).
    err = ModelPredictionError("boom", player_name="Gerrit Cole")
    assert str(err) == "boom"


# ---------------------------------------------------------------------------
# PickleLoadError
# ---------------------------------------------------------------------------


def test_pickle_load_without_reason():
    err = PickleLoadError("/p/file.pkl")
    assert err.file_path == "/p/file.pkl"
    assert err.reason is None
    assert str(err) == "Failed to load pickle file: /p/file.pkl"


def test_pickle_load_with_reason():
    err = PickleLoadError("/p/file.pkl", reason="truncated")
    assert str(err) == "Failed to load pickle file: /p/file.pkl - truncated"


# ---------------------------------------------------------------------------
# Raisability
# ---------------------------------------------------------------------------


def test_raise_and_catch_as_base():
    with pytest.raises(MLBError):
        raise ModelNotFoundError("/x")
    with pytest.raises(MLBError):
        raise DataValidationError("bad")
