"""
ObservaGuard — Log Traffic Simulator
Generates realistic log traffic with built-in anomaly spikes.
Sends logs to the ObservaGuard API via HTTP POST.

Usage:
  python simulator.py                      # run default scenario
  python simulator.py --scenario spike     # force immediate spike
  python simulator.py --scenario chaos     # continuous chaos mode
  python simulator.py --lines 200          # custom line count
  python simulator.py --service payments   # single-service focus
"""

import argparse
import random
import time
import json
import sys
from datetime import datetime, timedelta

try:
    import httpx
except ImportError:
    print("Install httpx first:  pip install httpx --break-system-packages")
    sys.exit(1)

API_BASE = "http://127.0.0.1:8080"

# ── Message templates per level ──────────────────────────────────────────────
MESSAGES = {
    "DEBUG": [
        "Cache lookup for key user_{id} hit",
        "DB query executed in {ms}ms: SELECT * FROM orders WHERE id={id}",
        "Payload received: {bytes} bytes from client {ip}",
        "Connection pool: {n}/{max} slots in use",
        "Feature flag 'new_checkout' evaluated: {flag}",
    ],
    "INFO": [
        "Request handled successfully: GET /api/v1/products ({ms}ms)",
        "User {id} authenticated via OAuth2",
        "Order {id} created for customer {cid}",
        "Scheduled job 'cleanup_sessions' completed — {n} rows removed",
        "Health check passed: DB latency {ms}ms",
        "Service started on port {port}",
        "Config reloaded: {n} keys updated",
        "Background worker processed {n} messages from queue",
    ],
    "WARNING": [
        "Slow query detected ({ms}ms): SELECT * FROM analytics WHERE date BETWEEN ...",
        "Memory usage at {pct}% — approaching threshold",
        "Retry #{n} for external API call to payment-gateway",
        "Rate limit approaching: {n}/{max} requests in window",
        "Deprecated endpoint /api/v1/legacy called by {ip}",
        "Cache miss rate elevated: {pct}% over last 60s",
    ],
    "ERROR": [
        "Unhandled exception in order-processor: NullPointerException at line {n}",
        "Database connection timeout after {ms}ms — host: db-primary:5432",
        "Payment gateway returned HTTP 502: Bad Gateway",
        "Failed to deserialize request body: unexpected token at position {n}",
        "Stripe API call failed: {code} — {msg}",
        "Redis connection refused — falling back to in-memory cache",
        "File upload failed: disk quota exceeded for tenant {id}",
        "JWT verification failed: token expired {n} seconds ago",
    ],
    "CRITICAL": [
        "FATAL: Primary database unreachable — failover initiated",
        "Out of memory — JVM heap exhausted, dumping core",
        "Security alert: brute-force detected from {ip} — {n} failed logins",
        "Data corruption detected in table 'transactions' — row {id}",
        "SSL certificate expired — all HTTPS connections failing",
        "Service mesh circuit breaker OPEN for payment-service",
    ],
}

SERVICES = ["api-gateway", "auth-service", "order-processor", "payment-service", "inventory", "notifications"]


def _rand_msg(level: str) -> str:
    tpl = random.choice(MESSAGES[level])
    return tpl.format(
        id=random.randint(1000, 9999),
        ms=random.randint(5, 4000),
        n=random.randint(1, 500),
        max=random.randint(50, 200),
        pct=random.randint(60, 99),
        ip=f"10.{random.randint(0,255)}.{random.randint(0,255)}.{random.randint(1,254)}",
        cid=random.randint(100, 999),
        bytes=random.randint(128, 16384),
        flag=random.choice(["true", "false"]),
        port=random.choice([8080, 8443, 3000]),
        code=random.choice(["rate_limited", "invalid_key", "timeout"]),
        msg=random.choice(["connection refused", "service unavailable", "timeout"]),
    )


def make_log_line(level: str, service: str, ts: datetime) -> str:
    msg = _rand_msg(level)
    return f"[{ts.strftime('%Y-%m-%d %H:%M:%S')}] {level} {service}: {msg}"


def send_bulk(lines: list[str], service: str, client: httpx.Client, verbose: bool = True):
    try:
        r = client.post(
            f"{API_BASE}/ingest/bulk",
            json={"lines": lines, "service": service},
            timeout=10,
        )
        data = r.json()
        alerts = sum(v.get("alerts_fired", 0) for v in data.get("detection", {}).values())
        if verbose:
            status_sym = "🚨" if alerts > 0 else "✓"
            print(
                f"  {status_sym} Sent {len(lines):3d} lines | service={service} | "
                f"alerts={alerts} | HTTP {r.status_code}"
            )
        return alerts
    except Exception as e:
        print(f"  ✗ Send failed: {e}")
        return 0


def scenario_normal(client: httpx.Client, service: str, n: int):
    """Healthy traffic: mostly INFO, some WARN, few ERROR."""
    print(f"\n[NORMAL] Generating {n} lines for '{service}'...")
    weights = {"DEBUG": 5, "INFO": 70, "WARNING": 18, "ERROR": 6, "CRITICAL": 1}
    population = []
    for lvl, w in weights.items():
        population.extend([lvl] * w)

    now = datetime.utcnow()
    lines = []
    for i in range(n):
        lvl = random.choice(population)
        ts  = now - timedelta(seconds=(n - i) * 0.5)
        lines.append(make_log_line(lvl, service, ts))

    send_bulk(lines, service, client)


def scenario_spike(client: httpx.Client, service: str):
    """Simulates a sudden error spike — should trigger ERROR_SPIKE alert."""
    print(f"\n[SPIKE] Building normal baseline then injecting spike for '{service}'...")

    # Phase 1: 3 windows of normal traffic to build baseline
    print("  Phase 1: Building baseline (3 windows × 30 logs)...")
    for w in range(3):
        now = datetime.utcnow()
        normal_lines = [
            make_log_line(
                random.choice(["INFO"] * 8 + ["WARNING"] * 1 + ["ERROR"] * 1),
                service,
                now - timedelta(seconds=(3 - w) * 60 + random.randint(0, 30))
            )
            for _ in range(30)
        ]
        send_bulk(normal_lines, service, client)
        time.sleep(0.3)

    # Phase 2: Inject spike — 70% errors
    print("  Phase 2: INJECTING ERROR SPIKE (70% error rate)...")
    now = datetime.utcnow()
    spike_lines = []
    for i in range(40):
        lvl = random.choice(
            ["ERROR"] * 50 + ["CRITICAL"] * 20 + ["WARNING"] * 15 + ["INFO"] * 15
        )
        spike_lines.append(make_log_line(lvl, service, now - timedelta(seconds=random.randint(0, 30))))

    total_alerts = send_bulk(spike_lines, service, client)
    if total_alerts > 0:
        print(f"  ✅ Spike detected! {total_alerts} alert(s) fired.")
    else:
        print("  ⚠  Spike sent — alert may need more baseline data.")


def scenario_chaos(client: httpx.Client, services: list[str], rounds: int):
    """Multi-service chaos: random spikes across services."""
    print(f"\n[CHAOS] Running {rounds} chaos rounds across {len(services)} services...")
    for r in range(1, rounds + 1):
        print(f"\n  Round {r}/{rounds}:")
        for svc in services:
            is_spiking = random.random() < 0.4
            if is_spiking:
                weights = ["ERROR"] * 55 + ["CRITICAL"] * 20 + ["WARNING"] * 15 + ["INFO"] * 10
            else:
                weights = ["INFO"] * 70 + ["WARNING"] * 20 + ["ERROR"] * 8 + ["DEBUG"] * 2
            now   = datetime.utcnow()
            lines = [
                make_log_line(random.choice(weights), svc, now - timedelta(seconds=random.randint(0, 30)))
                for _ in range(random.randint(15, 35))
            ]
            send_bulk(lines, svc, client)
            time.sleep(0.2)
        time.sleep(1)


def main():
    parser = argparse.ArgumentParser(description="ObservaGuard Log Simulator")
    parser.add_argument("--scenario", choices=["normal", "spike", "chaos", "demo"], default="demo",
                        help="Simulation scenario (default: demo)")
    parser.add_argument("--service",  default=None, help="Service name override")
    parser.add_argument("--lines",    type=int, default=100, help="Lines for normal scenario")
    parser.add_argument("--rounds",   type=int, default=5,   help="Rounds for chaos scenario")
    parser.add_argument("--port",     type=int, default=8080, help="ObservaGuard API port (default: 8080)")
    args = parser.parse_args()

    global API_BASE
    API_BASE = f"http://127.0.0.1:{args.port}"

    print("=" * 60)
    print("  ObservaGuard Log Simulator")
    print(f"  Target: {API_BASE}")
    print(f"  Scenario: {args.scenario.upper()}")
    print("=" * 60)

    with httpx.Client() as client:
        # Check API is up
        try:
            r = client.get(f"{API_BASE}/health", timeout=5)
            print(f"\n✓ API reachable (HTTP {r.status_code})")
        except Exception as e:
            print(f"\n✗ Cannot reach API at {API_BASE}: {e}")
            print("  Make sure ObservaGuard is running:  uvicorn main:app --reload")
            sys.exit(1)

        svc = args.service or random.choice(SERVICES)

        if args.scenario == "normal":
            scenario_normal(client, svc, args.lines)

        elif args.scenario == "spike":
            scenario_spike(client, args.service or "api-gateway")

        elif args.scenario == "chaos":
            scenario_chaos(client, SERVICES, args.rounds)

        elif args.scenario == "demo":
            # Full demo: all services with normal + spike
            print("\n[DEMO] Full system demo — 3 phases\n")

            # Phase 1: Normal traffic across all services
            print("Phase 1: Normal traffic (all services)...")
            for svc in SERVICES:
                scenario_normal(client, svc, 40)
                time.sleep(0.2)

            # Phase 2: Spike on api-gateway
            print("\nPhase 2: Error spike on api-gateway...")
            scenario_spike(client, "api-gateway")

            # Phase 3: Mini-chaos
            print("\nPhase 3: Multi-service chaos (3 rounds)...")
            scenario_chaos(client, ["payment-service", "auth-service", "order-processor"], 3)

    print("\n" + "=" * 60)
    print("  Simulation complete!")
    print(f"  View dashboard: {API_BASE}/dashboard")
    print(f"  Alerts:         {API_BASE}/alerts")
    print(f"  Metrics:        {API_BASE}/metrics")
    print("=" * 60)


if __name__ == "__main__":
    main()
