"""Anomaly detection for AWS cost data using statistical methods."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from sentry.costs import DailyCost


class Severity(str, Enum):
    """Alert severity levels."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class Anomaly:
    """Detected cost anomaly."""

    date: str
    actual_cost: float
    expected_cost: float
    z_score: float
    deviation_pct: float
    severity: Severity
    message: str


class AnomalyDetector:
    """Detects cost anomalies using z-score and rolling average methods."""

    def __init__(
        self,
        z_threshold: float = 2.0,
        rolling_window: int = 7,
        min_data_points: int = 5,
        critical_z: float = 4.0,
        high_z: float = 3.0,
        medium_z: float = 2.0,
    ):
        """Initialize the anomaly detector.

        Args:
            z_threshold: Minimum z-score to flag an anomaly.
            rolling_window: Number of days for rolling average calculation.
            min_data_points: Minimum data points needed for detection.
            critical_z: Z-score threshold for critical severity.
            high_z: Z-score threshold for high severity.
            medium_z: Z-score threshold for medium severity.
        """
        self.z_threshold = z_threshold
        self.rolling_window = rolling_window
        self.min_data_points = min_data_points
        self._severity_thresholds = [
            (critical_z, Severity.CRITICAL),
            (high_z, Severity.HIGH),
            (medium_z, Severity.MEDIUM),
        ]

    def detect(
        self,
        daily_costs: list[DailyCost],
        budget_threshold: Optional[float] = None,
    ) -> list[Anomaly]:
        """Detect anomalies in daily cost data.

        Uses two strategies:
        1. Z-score: flags costs that deviate significantly from the overall mean.
        2. Rolling average: flags costs that deviate from recent trend.

        Args:
            daily_costs: List of daily cost records.
            budget_threshold: Optional absolute budget threshold.

        Returns:
            List of detected anomalies sorted by severity.
        """
        if len(daily_costs) < self.min_data_points:
            return []

        amounts = [c.amount for c in daily_costs]
        anomalies: list[Anomaly] = []

        # Strategy 1: Z-score against full dataset
        mean = self._mean(amounts)
        std = self._stddev(amounts)

        if std > 0:
            for cost in daily_costs:
                z = (cost.amount - mean) / std
                if abs(z) >= self.z_threshold:
                    severity = self._classify_severity(abs(z))
                    deviation = ((cost.amount - mean) / mean * 100) if mean > 0 else 0
                    anomalies.append(
                        Anomaly(
                            date=cost.date.isoformat(),
                            actual_cost=round(cost.amount, 2),
                            expected_cost=round(mean, 2),
                            z_score=round(z, 3),
                            deviation_pct=round(deviation, 1),
                            severity=severity,
                            message=(
                                f"Cost ${cost.amount:.2f} is {abs(deviation):.1f}% "
                                f"{'above' if deviation > 0 else 'below'} average "
                                f"(${mean:.2f}), z-score: {z:.2f}"
                            ),
                        )
                    )

        # Strategy 2: Rolling average deviation
        rolling_anomalies = self._detect_rolling_anomalies(daily_costs, amounts)
        anomalies.extend(rolling_anomalies)

        # Strategy 3: Budget threshold
        if budget_threshold is not None:
            for cost in daily_costs:
                if cost.amount > budget_threshold:
                    already_flaged = any(a.date == cost.date.isoformat() for a in anomalies)
                    if not already_flaged:
                        pct_over = ((cost.amount - budget_threshold) / budget_threshold) * 100
                        anomalies.append(
                            Anomaly(
                                date=cost.date.isoformat(),
                                actual_cost=round(cost.amount, 2),
                                expected_cost=round(budget_threshold, 2),
                                z_score=0.0,
                                deviation_pct=round(pct_over, 1),
                                severity=Severity.HIGH if pct_over > 50 else Severity.MEDIUM,
                                message=(
                                    f"Cost ${cost.amount:.2f} exceeds budget "
                                    f"threshold ${budget_threshold:.2f} by {pct_over:.1f}%"
                                ),
                            )
                        )

        # Deduplicate by date, keep highest severity
        deduped = self._deduplicate(anomalies)
        severity_order = {
            Severity.CRITICAL: 0,
            Severity.HIGH: 1,
            Severity.MEDIUM: 2,
            Severity.LOW: 3,
        }
        return sorted(deduped, key=lambda a: severity_order[a.severity])

    def _detect_rolling_anomalies(
        self, daily_costs: list[DailyCost], amounts: list[float]
    ) -> list[Anomaly]:
        """Detect anomalies using rolling window average."""
        anomalies = []
        for i in range(self.rolling_window, len(amounts)):
            window = amounts[i - self.rolling_window : i]
            window_mean = self._mean(window)
            window_std = self._stddev(window)

            if window_std == 0 or window_mean == 0:
                continue

            z = (amounts[i] - window_mean) / window_std
            if abs(z) >= self.z_threshold:
                severity = self._classify_severity(abs(z))
                deviation = ((amounts[i] - window_mean) / window_mean) * 100
                anomalies.append(
                    Anomaly(
                        date=daily_costs[i].date.isoformat(),
                        actual_cost=round(amounts[i], 2),
                        expected_cost=round(window_mean, 2),
                        z_score=round(z, 3),
                        deviation_pct=round(deviation, 1),
                        severity=severity,
                        message=(
                            f"Cost ${amounts[i]:.2f} deviates {abs(deviation):.1f}% "
                            f"from {self.rolling_window}-day rolling average "
                            f"(${window_mean:.2f})"
                        ),
                    )
                )
        return anomalies

    def _classify_severity(self, abs_z: float) -> Severity:
        """Classify anomaly severity based on z-score magnitude."""
        for threshold, severity in self._severity_thresholds:
            if abs_z >= threshold:
                return severity
        return Severity.LOW

    def _deduplicate(self, anomalies: list[Anomaly]) -> list[Anomaly]:
        """Keep only the highest-severity anomaly per date."""
        by_date: dict[str, Anomaly] = {}
        severity_order = {
            Severity.CRITICAL: 0,
            Severity.HIGH: 1,
            Severity.MEDIUM: 2,
            Severity.LOW: 3,
        }
        for a in anomalies:
            existing = by_date.get(a.date)
            if existing is None or severity_order[a.severity] < severity_order[existing.severity]:
                by_date[a.date] = a
        return list(by_date.values())

    @staticmethod
    def _mean(values: list[float]) -> float:
        return sum(values) / len(values) if values else 0.0

    @staticmethod
    def _stddev(values: list[float]) -> float:
        if len(values) < 2:
            return 0.0
        mean = sum(values) / len(values)
        variance = sum((x - mean) ** 2 for x in values) / (len(values) - 1)
        return math.sqrt(variance)
