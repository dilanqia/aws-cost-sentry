"""AWS Cost Explorer client for fetching and analyzing daily costs."""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from typing import Optional

import boto3
from botocore.exceptions import ClientError, BotoCoreError


@dataclass
class DailyCost:
    """Represents cost for a single day."""

    date: datetime.date
    amount: float
    unit: str = "USD"
    service: Optional[str] = None


@dataclass
class CostSummary:
    """Aggregated cost data over a period."""

    daily_costs: list[DailyCost]
    total: float
    average: float
    period_start: datetime.date
    period_end: datetime.date


class CostExplorerClient:
    """Fetches cost data from AWS Cost Explorer API."""

    def __init__(
        self,
        profile: Optional[str] = None,
        region: str = "us-east-1",
        session: Optional[boto3.Session] = None,
    ):
        if session:
            self._session = session
        elif profile:
            self._session = boto3.Session(profile_name=profile, region_name=region)
        else:
            self._session = boto3.Session(region_name=region)

        self._client = self._session.client("ce")

    def get_daily_costs(
        self,
        days: int = 30,
        granularity: str = "DAILY",
        group_by_service: bool = False,
        filter_expression: Optional[dict] = None,
    ) -> CostSummary:
        """Fetch daily costs for the given number of days.

        Args:
            days: Number of days to look back.
            granularity: Cost granularity (DAILY or MONTHLY).
            group_by_service: Whether to break down by service.
            filter_expression: Optional Cost Explorer filter expression.

        Returns:
            CostSummary with daily breakdown and aggregates.
        """
        end_date = datetime.date.today()
        start_date = end_date - datetime.timedelta(days=days)

        params = {
            "TimePeriod": {
                "Start": start_date.isoformat(),
                "End": end_date.isoformat(),
            },
            "Granularity": granularity,
            "Metrics": ["UnblendedCost"],
        }

        if group_by_service:
            params["GroupBy"] = [
                {"Type": "DIMENSION", "Key": "SERVICE"},
            ]

        if filter_expression:
            params["Filter"] = filter_expression

        try:
            response = self._client.get_cost_and_usage(**params)
        except ClientError as e:
            raise CostExplorerError(f"Failed to fetch costs: {e}") from e
        except BotoCoreError as e:
            raise CostExplorerError(f"AWS connection error: {e}") from e

        daily_costs = []
        for result_by_time in response.get("ResultsByTime", []):
            date = datetime.date.fromisoformat(result_by_time["TimePeriod"]["Start"])
            for group in result_by_time.get("Groups", []):
                service = group["Keys"][0] if group.get("Keys") else None
                amount = float(group["Metrics"]["UnblendedCost"]["Amount"])
                daily_costs.append(DailyCost(date=date, amount=amount, service=service))

            if not result_by_time.get("Groups"):
                amount = float(result_by_time["Total"]["UnblendedCost"]["Amount"])
                daily_costs.append(DailyCost(date=date, amount=amount))

        # Handle pagination
        while response.get("NextPageToken"):
            response = self._client.get_cost_and_usage(
                **params, NextPageToken=response["NextPageToken"]
            )
            for result_by_time in response.get("ResultsByTime", []):
                date = datetime.date.fromisoformat(
                    result_by_time["TimePeriod"]["Start"]
                )
                amount = float(result_by_time["Total"]["UnblendedCost"]["Amount"])
                daily_costs.append(DailyCost(date=date, amount=amount))

        total = sum(c.amount for c in daily_costs)
        avg = total / len(daily_costs) if daily_costs else 0.0

        return CostSummary(
            daily_costs=daily_costs,
            total=round(total, 4),
            average=round(avg, 4),
            period_start=start_date,
            period_end=end_date,
        )

    def get_current_month_cost(self) -> float:
        """Get the total cost for the current month so far."""
        today = datetime.date.today()
        first_of_month = today.replace(day=1)

        try:
            response = self._client.get_cost_and_usage(
                TimePeriod={
                    "Start": first_of_month.isoformat(),
                    "End": today.isoformat(),
                },
                Granularity="MONTHLY",
                Metrics=["UnblendedCost"],
            )
            return float(
                response["ResultsByTime"][0]["Total"]["UnblendedCost"]["Amount"]
            )
        except (ClientError, BotoCoreError, KeyError, IndexError) as e:
            raise CostExplorerError(f"Failed to fetch current month cost: {e}") from e


class CostExplorerError(Exception):
    """Raised when Cost Explorer API calls fail."""
