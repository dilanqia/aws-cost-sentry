"""CLI entry point for AWS Cost Sentry."""

from __future__ import annotations

import json
import logging
import sys
from typing import Optional

import click

from sentry import __version__
from sentry.alerts import EmailAlerter, EmailConfig, SlackConfig, SlackWebhook
from sentry.anomaly import AnomalyDetector, Severity
from sentry.costs import CostExplorerClient


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


@click.group()
@click.version_option(version=__version__)
def cli() -> None:
    """AWS Cost Sentry — detect cost anomalies and send alerts."""


@cli.command()
@click.option("--days", default=30, help="Number of days to analyze.")
@click.option("--profile", default=None, help="AWS profile name.")
@click.option("--region", default="us-east-1", help="AWS region.")
@click.option("--z-threshold", default=2.0, help="Z-score threshold for anomalies.")
@click.option("--budget", default=None, type=float, help="Daily budget threshold in USD.")
@click.option("--rolling-window", default=7, help="Rolling average window in days.")
@click.option("--slack-webhook", default=None, envvar="SLACK_WEBHOOK_URL", help="Slack webhook URL.")
@click.option("--slack-channel", default=None, help="Slack channel override.")
@click.option("--email-to", multiple=True, help="Email recipients (can repeat).")
@click.option("--smtp-host", default=None, envvar="SMTP_HOST", help="SMTP server host.")
@click.option("--smtp-port", default=587, envvar="SMTP_PORT", help="SMTP server port.")
@click.option("--smtp-user", default=None, envvar="SMTP_USER", help="SMTP username.")
@click.option("--smtp-pass", default=None, envvar="SMTP_PASS", help="SMTP password.")
@click.option("--email-from", default=None, envvar="EMAIL_FROM", help="Sender email address.")
@click.option("--output", type=click.Choice(["text", "json"]), default="text", help="Output format.")
@click.option("--verbose", is_flag=True, help="Enable debug logging.")
def scan(
    days: int,
    profile: Optional[str],
    region: str,
    z_threshold: float,
    budget: Optional[float],
    rolling_window: int,
    slack_webhook: Optional[str],
    slack_channel: Optional[str],
    email_to: tuple[str, ...],
    smtp_host: Optional[str],
    smtp_port: int,
    smtp_user: Optional[str],
    smtp_pass: Optional[str],
    email_from: Optional[str],
    output: str,
    verbose: bool,
) -> None:
    """Scan AWS costs for anomalies and optionally send alerts."""
    _setup_logging(verbose)
    logger = logging.getLogger("sentry.cli")

    # Fetch costs
    click.echo(f"Fetching {days} days of cost data...", err=True)
    try:
        client = CostExplorerClient(profile=profile, region=region)
        summary = client.get_daily_costs(days=days)
    except Exception as e:
        click.echo(f"Error fetching costs: {e}", err=True)
        sys.exit(1)

    click.echo(
        f"  Period: {summary.period_start} to {summary.period_end}",
        err=True,
    )
    click.echo(f"  Total: ${summary.total:,.2f}", err=True)
    click.echo(f"  Daily avg: ${summary.average:,.2f}", err=True)

    # Detect anomalies
    detector = AnomalyDetector(
        z_threshold=z_threshold,
        rolling_window=rolling_window,
    )
    anomalies = detector.detect(summary.daily_costs, budget_threshold=budget)

    if not anomalies:
        click.echo("✅ No anomalies detected.", err=True)
        if output == "json":
            click.echo(json.dumps({"anomalies": [], "summary": _summary_dict(summary)}, indent=2))
        return

    click.echo(f"\n⚠️  Found {len(anomalies)} anomal{'y' if len(anomalies) == 1 else 'ies'}:\n", err=True)

    if output == "json":
        result = {
            "anomalies": [
                {
                    "date": a.date,
                    "actual_cost": a.actual_cost,
                    "expected_cost": a.expected_cost,
                    "z_score": a.z_score,
                    "deviation_pct": a.deviation_pct,
                    "severity": a.severity.value,
                    "message": a.message,
                }
                for a in anomalies
            ],
            "summary": _summary_dict(summary),
        }
        click.echo(json.dumps(result, indent=2))
    else:
        for a in anomalies:
            icon = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🔵"}.get(
                a.severity.value, "⚪"
            )
            click.echo(f"  {icon} [{a.severity.value.upper()}] {a.date}")
            click.echo(f"     Actual: ${a.actual_cost:,.2f}  Expected: ${a.expected_cost:,.2f}  ({a.deviation_pct:+.1f}%)")
            click.echo(f"     {a.message}")
            click.echo()

    # Send alerts
    sent_any = False

    if slack_webhook:
        click.echo("Sending Slack alert...", err=True)
        slack = SlackWebhook(SlackConfig(webhook_url=slack_webhook, channel=slack_channel))
        if slack.send(anomalies, _summary_dict(summary)):
            sent_any = True
        else:
            click.echo("  ❌ Slack alert failed", err=True)

    if email_to and smtp_host:
        click.echo("Sending email alert...", err=True)
        email_cfg = EmailConfig(
            smtp_host=smtp_host,
            smtp_port=smtp_port,
            username=smtp_user or "",
            password=smtp_pass or "",
            from_addr=email_from or "",
            to_addrs=list(email_to),
        )
        email = EmailAlerter(email_cfg)
        if email.send(anomalies, _summary_dict(summary)):
            sent_any = True
        else:
            click.echo("  ❌ Email alert failed", err=True)

    if sent_any:
        click.echo("✅ Alerts sent.", err=True)

    # Exit with error code if critical/high anomalies found
    max_severity = min(anomalies, key=lambda a: {"critical": 0, "high": 1, "medium": 2, "low": 3}[a.severity.value])
    if max_severity.severity in (Severity.CRITICAL, Severity.HIGH):
        sys.exit(2)


@cli.command()
@click.option("--profile", default=None, help="AWS profile name.")
@click.option("--region", default="us-east-1", help="AWS region.")
@click.option("--output", type=click.Choice(["text", "json"]), default="text")
def current(profile: Optional[str], region: str, output: str) -> None:
    """Show current month's cost so far."""
    client = CostExplorerClient(profile=profile, region=region)
    try:
        cost = client.get_current_month_cost()
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    if output == "json":
        click.echo(json.dumps({"current_month_cost": cost}))
    else:
        click.echo(f"Current month cost: ${cost:,.2f}")


def _summary_dict(summary) -> dict:
    return {
        "total": summary.total,
        "average": summary.average,
        "period_start": summary.period_start.isoformat(),
        "period_end": summary.period_end.isoformat(),
        "data_points": len(summary.daily_costs),
    }


def main() -> None:
    """Entry point."""
    cli()


if __name__ == "__main__":
    main()
