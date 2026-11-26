"""DriftService.check_drift: a feature vector against the reference recorded at training time."""

import pytest

from nba.core.drift_service import DriftService
from nba.core.reference_distributions import FeatureDistribution, ReferenceDistributions


def _dist(name, mean, std, lo, hi, missing_rate=0.0):
    return FeatureDistribution(
        name=name, mean=mean, std=std, min=lo, max=hi, p25=mean - std, p50=mean, p75=mean + std,
        missing_rate=missing_rate, unique_count=50,
    )  # fmt: skip


@pytest.fixture
def service(tmp_path):
    svc = DriftService("POINTS", reference_path=str(tmp_path / "absent.json"))
    assert svc.reference is None  # nothing at that path
    svc.reference = ReferenceDistributions(
        stat_type="POINTS",
        created_at="2026-01-01T00:00:00-05:00",
        training_samples=1000,
        features={
            "minutes": _dist("minutes", 30.0, 5.0, 10.0, 45.0),
            "pace": _dist("pace", 100.0, 3.0, 92.0, 110.0),
            "rest_days": _dist("rest_days", 2.0, 1.0, 0.0, 7.0),
            "often_missing": _dist("often_missing", 1.0, 1.0, 0.0, 5.0, missing_rate=0.6),
        },
    )
    return svc


def test_without_a_reference_nothing_is_checked(tmp_path):
    svc = DriftService("POINTS", reference_path=str(tmp_path / "absent.json"))
    result = svc.check_drift({"minutes": 30.0})
    assert result.checked_features == 0 and not result.has_drift


def test_values_like_training_show_no_drift(service):
    result = service.check_drift({"minutes": 31.0, "pace": 101.0, "rest_days": 2.0})
    assert result.drifted_features == [] and result.severity == "none"


def test_a_value_far_outside_the_training_range_is_drift(service):
    result = service.check_drift({"minutes": 31.0, "pace": 160.0, "rest_days": 2.0})
    assert result.drifted_features == ["pace"]
    assert "out_of_range" in result.drift_details["pace"]["reasons"]
    assert any(r.startswith("high_zscore_") for r in result.drift_details["pace"]["reasons"])
    assert result.severity == "high"  # one of three checked features


def test_a_feature_rarely_missing_in_training_and_missing_now_is_drift(service):
    """Regression: the reason was built and then discarded, so a missing input never counted."""
    result = service.check_drift({"minutes": None, "pace": float("nan"), "rest_days": 2.0})
    assert sorted(result.drifted_features) == ["minutes", "pace"]
    assert result.drift_details["minutes"]["reasons"] == ["unexpected_missing"]
    assert result.drift_details["minutes"]["value"] is None


def test_a_feature_often_missing_in_training_may_be_missing(service):
    result = service.check_drift({"minutes": 30.0, "often_missing": None})
    assert "often_missing" not in result.drifted_features


def test_missing_values_are_ignored_when_that_check_is_off(service):
    result = service.check_drift({"minutes": None, "rest_days": 2.0}, check_missing=False)
    assert result.drifted_features == []
