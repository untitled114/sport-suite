"""
Custom Exceptions for the MLB package
=====================================
Exception types for the MLB package.

Usage:
    from mlb.core.exceptions import (
        DataValidationError,
        ModelNotFoundError,
        ModelLoadError,
        ModelPredictionError,
        PickleLoadError,
    )
"""


class MLBError(Exception):
    """Base exception for all MLB package errors."""


# =============================================================================
# Model Errors
# =============================================================================


class ModelError(MLBError):
    """Base exception for model-related errors."""


class ModelNotFoundError(ModelError):
    """Raised when a required model file is not found."""

    def __init__(self, model_path: str, stat_type: str = None):  # noqa: B042
        self.model_path = model_path
        self.stat_type = stat_type
        message = f"Model not found: {model_path}"
        if stat_type:
            message = f"{stat_type} model not found: {model_path}"
        super().__init__(message)


class ModelLoadError(ModelError):
    """Raised when a model fails to load (corrupted, version mismatch, etc.)."""

    def __init__(self, model_path: str, reason: str = None):  # noqa: B042
        self.model_path = model_path
        self.reason = reason
        message = f"Failed to load model: {model_path}"
        if reason:
            message = f"{message} - {reason}"
        super().__init__(message)


class ModelPredictionError(ModelError):
    """Raised when model prediction fails."""

    def __init__(self, message: str, player_name: str = None, stat_type: str = None):  # noqa: B042
        self.player_name = player_name
        self.stat_type = stat_type
        if player_name and stat_type:
            message = f"{message} (player={player_name}, stat={stat_type})"
        super().__init__(message)


# =============================================================================
# Data Validation Errors
# =============================================================================


class DataValidationError(MLBError):
    """Base exception for data validation errors."""


# =============================================================================
# Serialization Errors
# =============================================================================


class SerializationError(MLBError):
    """Base exception for serialization-related errors."""


class PickleLoadError(SerializationError):
    """Raised when pickle deserialization fails."""

    def __init__(self, file_path: str, reason: str = None):  # noqa: B042
        self.file_path = file_path
        self.reason = reason
        message = f"Failed to load pickle file: {file_path}"
        if reason:
            message = f"{message} - {reason}"
        super().__init__(message)
