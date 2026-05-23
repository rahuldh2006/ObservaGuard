# ObservaGuard

**Intelligent Observability & Event Watchdog — SRE Edition**

ObservaGuard is a self-contained log-ingestion and anomaly-detection platform built with FastAPI. It accepts raw log lines from any service, parses them, stores them in SQLite, runs statistical anomaly detection, fires webhook alerts, and surfaces everything through a live HTML dashboard.

---

## Features

- **Multi-format log ingestion** — bracketed timestamps, key-value, syslog, plain-level, and JSON log formats all parsed automatically
- **Bulk ingest API** — send batches of log lines in a single HTTP call
- **Statistical anomaly detection** — Z-score-based spike detection per service window; fires `ERROR_SPIKE`, `CRITICAL_BURST`, and `ERROR_RATE_HIGH` alerts
- **Webhook alert system** — alerts POST to a configurable webhook endpoint; built-in mock receiver included
- **Live dashboard** — auto-refreshing HTML dashboard showing health per service, error-rate charts, and recent alerts
- **Log traffic simulator** — built-in simulator with `normal`, `spike`, `chaos`, and `demo` scenarios for testing
- **One-command startup** — `start.bat` (Windows) or `start.sh` (Linux/macOS) installs dependencies, starts the server, and runs the demo simulation
- **Full unit test suite** — 50+ tests across the parser, detector, API, and alert manager

---

## Tech Stack

| Layer | Technology |
|---|---|
| API framework | FastAPI 0.111 |
| ASGI server | Uvicorn 0.29 |
| Database | SQLite via SQLAlchemy 2.0 |
| Anomaly detection | NumPy (Z-score over sliding windows) |
| HTTP client (simulator & webhooks) | httpx 0.27 |
| Templating | Jinja2 3.1 |
| Data validation | Pydantic 2.7 |
| Testing | pytest + pytest-cov |

---

## Quick Start

### Windows

```bat
start.bat
```

The script will:
1. Install all Python dependencies via `pip`
2. Auto-select an available port (prefers **8080**, falls back to **8081**)
3. Set `OBSERVAGUARD_PORT` so webhooks post back to the correct port
4. Launch the ObservaGuard server in a separate window
5. Wait until the server is ready, then run the full demo simulation

### Linux / macOS

```bash
chmod +x start.sh
./start.sh
```

### Manual start

```bash
pip install -r requirements.txt
export OBSERVAGUARD_PORT=8080
python -m uvicorn app.main:app --host 0.0.0.0 --port 8080 --reload
# In a second terminal:
python simulator.py --scenario demo --port 8080
```

---

## Dashboard

The dashboard at `/dashboard` auto-refreshes every **10 seconds**.

### Header
Displays the overall system status badge — **HEALTHY** (green), **WARNING** (yellow), or **DEGRADED** (red) — derived from the worst status across all monitored services. Also shows the auto-refresh interval.

### KPI Cards
Four at-a-glance counters updated on every refresh:

| Card | What it shows |
|---|---|
| **Total Logs Ingested** | Sum of `total_logs` across all metric snapshots in the last 60 minutes |
| **Active Alerts (10m)** | Number of `AlertEvent` rows triggered in the last 10 minutes |
| **Services Monitored** | Count of distinct services that have sent at least one log |
| **Peak Error Rate (1h)** | Highest error-rate percentage seen across all services in the rolling 60-minute window; turns yellow above 20 %, red above 55 % |

### Error Rate % — Rolling 60 min
A multi-line time-series chart (Chart.js) with one line per service. Each point is the error rate (%) recorded in a `MetricSnapshot`. The x-axis shows wall-clock time; the y-axis is capped at 100 %. Helps spot rising error trends before they cross alert thresholds.

### Service Health Sidebar
A per-service status bar grid. For each known service it shows:
- **Service name** (colour-coded, unique per service)
- **Status label** — `HEALTHY` (error rate < 20 %), `WARNING` (20–54 %), `DEGRADED` (≥ 55 % or anomaly flag set)
- **Horizontal bar** proportional to error rate (green / yellow / red)
- **Numeric error rate %**

### Anomaly Z-Score — Rolling 60 min
A multi-line chart showing the statistical Z-score per service over the last 60 minutes. A **dashed red threshold line at 2.5 σ** marks the anomaly trigger boundary. Values above this line indicate a statistically significant error spike relative to the historical baseline.

### Webhook Feed
A live scrolling feed of the **20 most recent** inbound webhook payloads received at `/webhook/receive`. Each entry shows:
- Timestamp
- Alert type (e.g. `ERROR_SPIKE`)
- Service name
- Severity level
- Error rate at time of firing

> **Note:** The Webhook Feed only populates when the server is started with the `OBSERVAGUARD_PORT` environment variable matching the actual listening port (handled automatically by `start.bat` / `start.sh`). Without it, webhook POSTs go to the wrong port and the feed stays empty.

### Alert Event Log
A sortable table of the last **50 alert events** (descending by time) with columns:

| Column | Description |
|---|---|
| Time | Wall-clock trigger time |
| Service | Source service name |
| Type | `ERROR_SPIKE`, `CRITICAL_BURST`, or `ERROR_RATE_HIGH` |
| Severity | `LOW`, `MEDIUM`, `HIGH`, or `CRITICAL` |
| Error Rate | Error % at the moment the alert fired |
| Z-Score | Standard deviations above historical mean |
| Webhook | HTTP status of the webhook POST (✓ 200, or —) |
| Message | Human-readable description of the anomaly |

---

## API Reference

### POST `/ingest`
Ingest a single raw log line.
```json
{ "log_line": "[2024-01-15 12:34:56] ERROR api-gateway: timeout", "service": "api-gateway" }
```

### POST `/ingest/bulk`
Ingest multiple log lines at once.
```json
{ "lines": ["...", "..."], "service": "api-gateway" }
```

### GET `/health`
Returns current health status (`HEALTHY` / `WARNING` / `DEGRADED`) per service based on the latest metric snapshot.

### GET `/metrics?service=api-gateway&minutes=60`
Returns time-series metric snapshots (error rate, Z-score, anomaly flag).

### GET `/alerts?limit=50&service=api-gateway`
Returns recent alert events with webhook delivery status.

### GET `/services`
Lists all services that have ingested logs.

### POST `/webhook/receive`
Mock webhook receiver — stores incoming alert payloads in memory.

### GET `/webhook/history`
Returns the last 20 received webhook payloads.

---

## Log Simulator

```
python simulator.py [--scenario SCENARIO] [--port PORT] [--service SERVICE] [--lines N] [--rounds N]

Scenarios:
  normal   Healthy traffic (70% INFO, 18% WARN, 6% ERROR, 1% CRITICAL)
  spike    Builds a normal baseline, then injects a sudden 70% error-rate spike
  chaos    Multi-service random spikes across all services
  demo     Full 3-phase demo: normal traffic + spike + chaos (default)
```

---

## Running Tests

```bash
# Install dev dependencies (includes pytest)
pip install -r requirements-dev.txt

# Run all tests with coverage report
pytest tests/ -v --cov=app --cov-report=term-missing

# Run a specific test module
pytest tests/test_log_parser.py -v
```

Tests use an in-memory SQLite database — no files are written to disk.

---

## Project Structure

```
ObservaGuard/
├── app/                        # Core application package
│   ├── __init__.py
│   ├── main.py                 # FastAPI app & all API routes
│   ├── models.py               # SQLAlchemy ORM models
│   ├── database.py             # SQLite engine, session factory, init_db()
│   ├── log_parser.py           # Multi-format log line parser
│   ├── anomaly_detector.py     # Z-score anomaly detection & snapshots
│   └── alert_manager.py        # Webhook alert firing
├── templates/
│   └── dashboard.html          # Live HTML dashboard (Chart.js, auto-refresh)
├── tests/
│   ├── conftest.py             # Shared pytest fixtures (in-memory test DB)
│   ├── test_log_parser.py      # Log parser unit tests
│   ├── test_anomaly_detector.py# Detector & Z-score unit tests
│   ├── test_api.py             # API endpoint integration tests
│   └── test_alert_manager.py   # Webhook & alert manager unit tests
├── simulator.py                # Log traffic simulator
├── requirements.txt            # Runtime dependencies
├── requirements-dev.txt        # Dev/test dependencies
├── start.bat                   # Windows one-command launcher
├── start.sh                    # Linux/macOS one-command launcher
├── .gitignore
└── LICENSE
```

---

## How Anomaly Detection Works

1. After every `/ingest/bulk` call, a **MetricSnapshot** is recorded for each service — counting total logs, error/critical counts, and error rate over a 60-second window.
2. The last 10 snapshots form a **baseline**. The current window's error rate is compared to the baseline mean using a **Z-score**.
3. Three alert types can fire:
   - `ERROR_SPIKE` — Z-score ≥ 2.5 σ (sudden relative jump vs. baseline)
   - `CRITICAL_BURST` — ≥ 3 CRITICAL/FATAL logs in the 60-second window
   - `ERROR_RATE_HIGH` — raw error rate ≥ 35 % when Z-score is low (sustained, not a spike)
4. A **120-second cooldown** prevents the same alert type from re-firing for the same service.
5. Each alert POSTs a webhook to `/webhook/receive` and is persisted as an `AlertEvent` in SQLite.

---

## License

MIT — see [LICENSE](LICENSE) for details.
