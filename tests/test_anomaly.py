"""Tests for the anomaly detection module."""

import datetime

import pytest

from sentry.anomaly import AnomalyDetector, Severity
from sentry.costs import DailyCost


def _make_costs(amounts: list[float], start: str = "2026-01-01") -> list[DailyCost]:
    """Helper to create a list of DailyCost objects."""
    base = datetime.date.fromisoformat(start)
    return [
        DailyCost(date=base + datetime.timedelta(days=i), amount=a)
        for i, a in enumerate(amounts)
    ]


class TestAnomalyDetector:
    """Test suite for AnomalyDetector."""

    def test_no_anomaly_on_flat_data(self):
        """Flat cost data should produce no anomalies."""
        costs = _make_costs([100.0] * 15)
        detector = AnomalyDetector(z_threshold=2.0)
        anomalies = detector.detect(costs)
        assert anomalies == []

    def test_detects_spike(self):
        """A clear spike should be detected."""
        costs = _make_costs([100.0] * 14 + [500.0])
        detector = AnomalyDetector(z_threshold=2.0)
        anomalies = detector.detect(costs)
        assert len(anomalies) >= 1
        assert any(a.date == costs[-1].date.isoformat() for a in anomalies)
        spike = [a for a in anomalies if a.date == costs[-1].date.isoformat()][0]
        assert spike.severity in (Severity.HIGH, Severity.CRITICAL)
        assert spike.actual_cost == 500.0

    def test_detects_drop(self):
        """A sudden drop should also be detected."""
        costs = _make_costs([200.0] * 14 + [10.0])
        detector = AnomalyDetector(z_threshold=2.0)
        anomalies = detector.detect(costs)
        assert len(anomalies) >= 1

    def test_budget_threshold(self):
        """Costs above budget threshold should be flagged."""
        costs = _make_costs([50.0, 60.0, 55.0, 45.0, 52.0, 48.0, 53.0])
        detector = AnomalyDetector(z_threshold=2.0)
        anomalies = detector.detect(costs, budget_threshold=40.0)
        assert len(anomalies) > 0
        assert all(a.severity in (Severity.MEDIUM, Severity.HIGH) for a in anomalies)

    def test_insufficient_data(self):
        """Should return empty list with too few data points."""
        costs = _make_costs([100.0, 200.0])
        detector = AnomalyDetector(min_data_points=5)
        anomalies = detector.detect(costs)
        assert anomalies == []

    def test_severity_classification(self):
        """Z-scores should map to correct severity levels."""
        detector = AnomalyDetector(
            critical_z=4.0, high_z=3.0, medium_z=2.0
        )
        assert detector._classify_severity(5.0) == Severity.CRITICAL
        assert detector._classify_severity(3.5) == Severity.HIGH
        assert detector._classify_severity(2.5) == Severity.MEDIUM
        assert detector._classify_severity(1.0) == Severity.LOW

    def test_rolling_window_detection(self):
        """Anomaly after a stable period should be detected via rolling average."""
        # 14 days stable at ~100, then spike to 300
        stable = [100.0 + (i % 3) * 5 for i in range(14)]
        costs = _make_costs(stable + [300.0])
        detector = AnomalyDetector(z_threshold=2.0, rolling_window=7)
        anomalies = detector.detect(costs)
        assert len(anomalies) >= 1
        assert any("rolling" in a.message.lower() or a.actual_cost == 300.0 for a in anomalies)

    def test_sorted_by_severity(self):
        """Anomalies should be sorted with highest severity first."""
        # Create data with multiple anomalies
        costs = _make_costs([100.0] * 10 + [200.0, 600.0])
        detector = AnomalyDetector(z_threshold=1.5)
        anomalies = detector.detect(costs)
        if len(anomalies) > 1:
            severity_order = {
                Severity.CRITICAL: 0, Severity.HIGH: 1, Severity.MEDIUM: 2, Severity.LOW: 3
            }
            for i in range(len(anomalies) - 1):
                assert severity_order[anomalies[i].severity] <= severity_order[anomalies[i + 1].severity]

    def test_custom_thresholds(self):
        """Custom z-threshold should affect detection sensitivity."""
        costs = _make_costs([100.0] * 14 + [180.0])
        strict = AnomalyDetector(z_threshold=3.0)
        loose = AnomalyDetector(z_threshold=1.0)
        strict_anomalies = strict.detect(costs)
        loose_anomalies = loose.detect(costs)
        # Loose threshold should detect more or equal
        assert len(loose_anomalies) >= len(strict_anomalies)

    def test_deduplication(self):
        """Same date should not appear twice in results."""
        costs = _make_costs([100.0] * 10 + [500.0])
        detector = AnomalyDetector(z_threshold=1.5, rolling_window=5)
        anomalies = detector.detect(costs)
        dates = [a.date for a in anomalies]
        assert len(dates) == len(set(dates))


class TestDailyCost:
    """Test DailyCost dataclass."""

    def test_creation(self):
        cost = DailyCost(date=datetime.date(2026, 1, 15), amount=123.45)
        assert cost.amount == 123.45
        assert cost.unit == "USD"
        assert cost.service is None

    def test_with_service(self):
        cost = DailyCost(
            date=datetime.date(2026, 1, 15),
            amount=50.0,
            service="Amazon EC2",
        )
        assert cost.service == "Amazon EC2"
