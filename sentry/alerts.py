"""Alert delivery via Slack webhooks and email."""

from __future__ import annotations

import json
import logging
import smtplib
from dataclasses import dataclass
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

import requests

from sentry.anomaly import Anomaly, Severity

logger = logging.getLogger(__name__)

SEVERITY_EMOJI = {
    Severity.CRITICAL: "🔴",
    Severity.HIGH: "🟠",
    Severity.MEDIUM: "🟡",
    Severity.LOW: "🔵",
}


@dataclass
class SlackConfig:
    """Slack webhook configuration."""

    webhook_url: str
    channel: Optional[str] = None
    username: str = "AWS Cost Sentry"


@dataclass
class EmailConfig:
    """Email SMTP configuration."""

    smtp_host: str
    smtp_port: int = 587
    username: str = ""
    password: str = ""
    from_addr: str = ""
    to_addrs: list[str] | None = None
    use_tls: bool = True


class SlackWebhook:
    """Sends anomaly alerts to Slack via incoming webhook."""

    def __init__(self, config: SlackConfig):
        self.config = config

    def send(self, anomalies: list[Anomaly], summary: Optional[dict] = None) -> bool:
        """Send anomaly alert to Slack.

        Args:
            anomalies: List of detected anomalies.
            summary: Optional cost summary dict with total, average, period.

        Returns:
            True if the message was sent successfully.
        """
        if not anomalies:
            return True

        blocks = [self._header_block()]
        blocks.append({"type": "divider"})

        if summary:
            blocks.append(self._summary_block(summary))
            blocks.append({"type": "divider"})

        for anomaly in anomalies[:10]:  # Slack has block limits
            blocks.append(self._anomaly_block(anomaly))

        if len(anomalies) > 10:
            blocks.append(
                {
                    "type": "context",
                    "elements": [
                        {
                            "type": "mrkdwn",
                            "text": f"_...and {len(anomalies) - 10} more anomalies_",
                        }
                    ],
                }
            )

        payload = {
            "username": self.config.username,
            "blocks": blocks,
        }
        if self.config.channel:
            payload["channel"] = self.config.channel

        try:
            resp = requests.post(
                self.config.webhook_url,
                json=payload,
                timeout=10,
            )
            resp.raise_for_status()
            logger.info("Slack alert sent successfully")
            return True
        except requests.RequestException as e:
            logger.error("Failed to send Slack alert: %s", e)
            return False

    def _header_block(self) -> dict:
        return {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": "⚠️ AWS Cost Anomaly Detected",
                "emoji": True,
            },
        }

    def _summary_block(self, summary: dict) -> dict:
        text = (
            f"*Period:* {summary.get('period_start', 'N/A')} → {summary.get('period_end', 'N/A')}\n"
            f"*Total:* ${summary.get('total', 0):,.2f}\n"
            f"*Daily Average:* ${summary.get('average', 0):,.2f}"
        )
        return {
            "type": "section",
            "text": {"type": "mrkdwn", "text": text},
        }

    def _anomaly_block(self, anomaly: Anomaly) -> dict:
        emoji = SEVERITY_EMOJI.get(anomaly.severity, "⚪")
        text = (
            f"{emoji} *{anomaly.severity.value.upper()}* — {anomaly.date}\n"
            f"Actual: `${anomaly.actual_cost:,.2f}` | "
            f"Expected: `${anomaly.expected_cost:,.2f}` | "
            f"Deviation: `{anomaly.deviation_pct:+.1f}%`\n"
            f"_{anomaly.message}_"
        )
        return {
            "type": "section",
            "text": {"type": "mrkdwn", "text": text},
        }


class EmailAlerter:
    """Sends anomaly alerts via SMTP email."""

    def __init__(self, config: EmailConfig):
        self.config = config

    def send(self, anomalies: list[Anomaly], summary: Optional[dict] = None) -> bool:
        """Send anomaly alert email.

        Args:
            anomalies: List of detected anomalies.
            summary: Optional cost summary dict.

        Returns:
            True if the email was sent successfully.
        """
        if not anomalies or not self.config.to_addrs:
            return True

        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"⚠️ AWS Cost Alert — {len(anomalies)} anomal{'y' if len(anomalies) == 1 else 'ies'} detected"
        msg["From"] = self.config.from_addr
        msg["To"] = ", ".join(self.config.to_addrs)

        text_body = self._build_text_body(anomalies, summary)
        html_body = self._build_html_body(anomalies, summary)

        msg.attach(MIMEText(text_body, "plain"))
        msg.attach(MIMEText(html_body, "html"))

        try:
            with smtplib.SMTP(self.config.smtp_host, self.config.smtp_port) as server:
                if self.config.use_tls:
                    server.starttls()
                if self.config.username and self.config.password:
                    server.login(self.config.username, self.config.password)
                server.sendmail(
                    self.config.from_addr,
                    self.config.to_addrs,
                    msg.as_string(),
                )
            logger.info("Email alert sent to %s", self.config.to_addrs)
            return True
        except smtplib.SMTPException as e:
            logger.error("Failed to send email alert: %s", e)
            return False

    def _build_text_body(self, anomalies: list[Anomaly], summary: Optional[dict]) -> str:
        lines = ["AWS Cost Anomaly Report", "=" * 40, ""]
        if summary:
            lines.extend([
                f"Period: {summary.get('period_start', 'N/A')} to {summary.get('period_end', 'N/A')}",
                f"Total Cost: ${summary.get('total', 0):,.2f}",
                f"Daily Average: ${summary.get('average', 0):,.2f}",
                "",
            ])
        lines.append(f"Detected {len(anomalies)} anomal{'y' if len(anomalies) == 1 else 'ies'}:")
        lines.append("")
        for a in anomalies:
            lines.extend([
                f"[{a.severity.value.upper()}] {a.date}",
                f"  Actual: ${a.actual_cost:,.2f}  |  Expected: ${a.expected_cost:,.2f}  |  Deviation: {a.deviation_pct:+.1f}%",
                f"  {a.message}",
                "",
            ])
        return "\n".join(lines)

    def _build_html_body(self, anomalies: list[Anomaly], summary: Optional[dict]) -> str:
        severity_colors = {
            Severity.CRITICAL: "#dc2626",
            Severity.HIGH: "#ea580c",
            Severity.MEDIUM: "#ca8a04",
            Severity.LOW: "#2563eb",
        }

        rows = ""
        for a in anomalies:
            color = severity_colors.get(a.severity, "#6b7280")
            rows += f"""
            <tr>
                <td style="padding:8px;border:1px solid #e5e7eb;">
                    <span style="color:{color};font-weight:bold;">{a.severity.value.upper()}</span>
                </td>
                <td style="padding:8px;border:1px solid #e5e7eb;">{a.date}</td>
                <td style="padding:8px;border:1px solid #e5e7eb;">${a.actual_cost:,.2f}</td>
                <td style="padding:8px;border:1px solid #e5e7eb;">${a.expected_cost:,.2f}</td>
                <td style="padding:8px;border:1px solid #e5e7eb;">{a.deviation_pct:+.1f}%</td>
            </tr>"""

        summary_html = ""
        if summary:
            summary_html = f"""
            <div style="background:#f9fafb;padding:12px;border-radius:6px;margin-bottom:16px;">
                <strong>Period:</strong> {summary.get('period_start', 'N/A')} → {summary.get('period_end', 'N/A')}<br>
                <strong>Total:</strong> ${summary.get('total', 0):,.2f}<br>
                <strong>Daily Average:</strong> ${summary.get('average', 0):,.2f}
            </div>"""

        return f"""
        <html><body style="font-family:system-ui,sans-serif;max-width:700px;margin:0 auto;padding:20px;">
            <h2 style="color:#111827;">⚠️ AWS Cost Anomaly Report</h2>
            {summary_html}
            <table style="width:100%;border-collapse:collapse;">
                <thead>
                    <tr style="background:#f3f4f6;">
                        <th style="padding:8px;border:1px solid #e5e7eb;text-align:left;">Severity</th>
                        <th style="padding:8px;border:1px solid #e5e7eb;text-align:left;">Date</th>
                        <th style="padding:8px;border:1px solid #e5e7eb;text-align:left;">Actual</th>
                        <th style="padding:8px;border:1px solid #e5e7eb;text-align:left;">Expected</th>
                        <th style="padding:8px;border:1px solid #e5e7eb;text-align:left;">Deviation</th>
                    </tr>
                </thead>
                <tbody>{rows}</tbody>
            </table>
            <p style="color:#6b7280;font-size:12px;margin-top:16px;">Generated by AWS Cost Sentry</p>
        </body></html>"""
