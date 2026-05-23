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

---

## Tech Stack

| Layer | Technology |
|---|---|
| API framework | FastAPI 0.111 |
| ASGI server | Uvicorn 0.29 |
| Database | SQLite via SQLAlchemy 2.0 |
| Anomaly detection | NumPy (Z-score over sliding windows) |
| HTTP client (simulator) | httpx 0.27 |
| Templating | Jinja2 3.1 |
| Data validation | Pydantic 2.7 |

---

## Quick Start

### Windows

```bat
start.bat
```

The script will:
1. Install all Python dependencies via `pip`
2. Auto-select an available port (prefers **8080**, falls back to **8081**)
3. Launch the ObservaGuard server in a separate window
4. Wait until the server is ready, then run the full demo simulation

### Linux / macOS

```bash
chmod +x start.sh
./start.sh
```

### Manual start

```bash
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8080 --reload
# In a second terminal:
python simulator.py --scenario demo --port 8080
```

---

## Dashboard & URLs

Once running, open your browser to:

| Page | URL |
|---|---|
| Live Dashboard | http://localhost:8080/dashboard |
| Alert History | http://localhost:8080/alerts |
| Metrics API | http://localhost:8080/metrics |
| Health API | http://localhost:8080/health |
| Interactive API Docs | http://localhost:8080/docs |

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

## Project Structure

```
ObservaGuard/
├── main.py               # FastAPI application & all API routes
├── models.py             # SQLAlchemy ORM models (LogEntry, MetricSnapshot, AlertEvent)
├── database.py           # SQLite engine, session factory, init_db()
├── log_parser.py         # Multi-format log line parser
├── anomaly_detector.py   # Z-score anomaly detection & metric snapshot recording
├── alert_manager.py      # Webhook alert firing
├── simulator.py          # Log traffic simulator (normal / spike / chaos / demo)
├── templates/
│   └── dashboard.html    # Live HTML dashboard (Chart.js, auto-refresh)
├── requirements.txt      # Python dependencies
├── start.bat             # Windows one-command launcher
└── start.sh              # Linux/macOS one-command launcher
```

---

## How Anomaly Detection Works

1. After every `/ingest/bulk` call, a **MetricSnapshot** is recorded for each service — counting total logs, error/critical counts, and error rate over a 60-second window.
2. The last 10 snapshots form a **baseline**. The current window's error rate is compared to the baseline mean using a **Z-score**.
3. Three alert types can fire:
   - `ERROR_SPIKE` — Z-score exceeds threshold (sudden relative jump)
   - `CRITICAL_BURST` — absolute critical log count exceeds limit
   - `ERROR_RATE_HIGH` — raw error rate exceeds a fixed threshold (≥ 55%)
4. Each alert POSTs a webhook to `/webhook/receive` and is persisted as an `AlertEvent` in SQLite.

---

## License

MIT — see [LICENSE](LICENSE) for details.
