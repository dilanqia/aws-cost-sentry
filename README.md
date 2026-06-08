# AWS Cost Sentry

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-pytest-blueviolet)](https://docs.pytest.org/)

**Detect AWS cost anomalies before they burn a hole in your budget.**

AWS Cost Sentry monitors your AWS spending via the Cost Explorer API, applies statistical anomaly detection (z-score + rolling average), and alerts you through Slack or email when costs spike unexpectedly.

## Features

- 📊 **Z-score anomaly detection** — flags costs that deviate significantly from historical patterns
- 📈 **Rolling average analysis** — catches sudden changes relative to recent trends
- 💰 **Budget threshold alerts** — get notified when daily costs exceed a fixed limit
- 💬 **Slack integration** — rich block-kit messages with severity indicators
- 📧 **Email reports** — HTML-formatted anomaly reports via SMTP
- 🎯 **Severity classification** — Critical / High / Medium / Low based on deviation magnitude
- 🖥️ **CLI-first** — simple `cost-sentry scan` command, CI-friendly exit codes

## Installation

```bash
pip install .

# Or with dev dependencies
pip install -e ".[dev]"
```

## Quick Start

```bash
# Scan last 30 days, output to terminal
cost-sentry scan --profile my-aws-profile

# Set a $500/day budget threshold with Slack alerts
cost-sentry scan \
  --days 30 \
  --budget 500 \
  --slack-webhook https://hooks.slack.com/services/T.../B.../xxx

# JSON output for automation
cost-sentry scan --output json --z-threshold 2.5

# Check current month spend
cost-sentry current --profile production
```

## Usage

### `cost-sentry scan`

Analyze AWS costs and detect anomalies.

| Flag | Default | Description |
|------|---------|-------------|
| `--days` | `30` | Number of days to analyze |
| `--profile` | — | AWS CLI profile name |
| `--region` | `us-east-1` | AWS region |
| `--z-threshold` | `2.0` | Z-score threshold for anomaly detection |
| `--budget` | — | Daily budget threshold (USD) |
| `--rolling-window` | `7` | Rolling average window size (days) |
| `--slack-webhook` | `$SLACK_WEBHOOK_URL` | Slack incoming webhook URL |
| `--slack-channel` | — | Override Slack channel |
| `--email-to` | — | Email recipient(s), repeatable |
| `--smtp-host` | `$SMTP_HOST` | SMTP server hostname |
| `--smtp-port` | `587` | SMTP server port |
| `--smtp-user` | `$SMTP_USER` | SMTP username |
| `--smtp-pass` | `$SMTP_PASS` | SMTP password |
| `--email-from` | `$EMAIL_FROM` | Sender email address |
| `--output` | `text` | Output format: `text` or `json` |
| `--verbose` | — | Enable debug logging |

**Exit codes:**
- `0` — No anomalies or low/medium severity only
- `1` — Error (API failure, credentials, etc.)
- `2` — High or critical severity anomalies detected

### `cost-sentry current`

Show current month-to-date cost.

```bash
cost-sentry current --profile my-profile --output json
# {"current_month_cost": 1234.56}
```

## Environment Variables

| Variable | Description |
|----------|-------------|
| `SLACK_WEBHOOK_URL` | Slack webhook URL (alternative to `--slack-webhook`) |
| `SMTP_HOST` | SMTP server hostname |
| `SMTP_PORT` | SMTP port (default: 587) |
| `SMTP_USER` | SMTP username |
| `SMTP_PASS` | SMTP password |
| `EMAIL_FROM` | Sender email address |

## How Anomaly Detection Works

1. **Data Collection** — Fetches daily unblended costs from AWS Cost Explorer
2. **Z-Score Analysis** — Computes z-score for each day against the full period's mean and standard deviation
3. **Rolling Average** — Compares each day against a configurable rolling window (default: 7 days)
4. **Budget Check** — Flags any day exceeding the optional budget threshold
5. **Deduplication** — Merges overlapping detections, keeping the highest severity per date
6. **Classification** — Maps z-score magnitudes to severity levels (configurable thresholds)

## Running Tests

```bash
pytest
pytest --cov=sentry --cov-report=term-missing
```

## License

[MIT](LICENSE)
