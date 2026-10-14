"""Run real loopback HTTP checks and a small local latency sample; never follow target redirects."""
from __future__ import annotations

import argparse
import json
import math
import statistics
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

from live_server import LiveServer

ROOT = Path(__file__).resolve().parents[1]


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def run(output: Path) -> dict:
    checks = []
    with LiveServer(output.parent / "http-server.log") as server:
        with httpx.Client(base_url=server.url, timeout=10, follow_redirects=False, trust_env=False) as client:
            headers = {"X-API-Key": server.key, "Idempotency-Key": "real-http-demo-0001"}
            require(client.get("/health/ready").status_code == 200, "Readiness failed")
            checks.append("live server readiness")
            first = client.post("/api/v1/links", headers=headers, json={"url": "https://example.com/demo?source=smoke"})
            require(first.status_code == 201, "Expected first creation 201")
            replay = client.post("/api/v1/links", headers=headers, json={"url": "https://example.com/demo?source=smoke"})
            require(replay.status_code == 200 and replay.json() == first.json(), "Idempotent replay failed")
            checks.append("201 create and 200 identical idempotent replay")
            code = first.json()["code"]
            path = f"/r/{code}"
            require(client.head(path).status_code == 302, "HEAD failed")
            stats_path = f"/api/v1/links/{code}/stats?days=3"
            require(client.get(stats_path, headers=headers).json()["total_redirects"] == 0, "HEAD changed analytics")
            checks.append("HEAD uncounted")
            for _ in range(10):
                require(client.get(path).status_code == 302, "Warm-up redirect failed")
            latencies = []
            def redirect(_: int) -> tuple[int, float, str]:
                start = time.perf_counter()
                result = client.get(path)
                return result.status_code, (time.perf_counter() - start) * 1000, result.headers.get("location", "")
            start = time.perf_counter()
            with ThreadPoolExecutor(max_workers=8) as pool:
                results = list(pool.map(redirect, range(64)))
            elapsed = time.perf_counter() - start
            for status, duration, location in results:
                require(status == 302 and location == "https://example.com/demo?source=smoke", "Concurrent redirect failed")
                latencies.append(duration)
            stats = client.get(stats_path, headers=headers).json()
            require(stats["total_redirects"] == 74, "Concurrent counters lost or added updates")
            require(sum(day["redirects"] for day in stats["daily"]) == 74, "Daily count differs")
            checks.append("64 concurrent measured redirects plus 10 warm-up counted exactly")
            require(client.get(stats_path).status_code == 401, "Private analytics exposed")
            require(client.post("/api/v1/links", headers=headers, json={"url": "javascript:alert(1)"}).status_code == 422, "Unsafe URL accepted")
            require(client.delete(f"/api/v1/links/{code}", headers=headers).status_code == 204, "Revocation failed")
            require(client.get(path).status_code == 410, "Revoked redirect remained active")
            checks.extend(["analytics authorization", "unsafe URL rejection", "revocation blocks redirect"])
            ordered = sorted(latencies)
            report = {"outcome": "PASS", "execution": "real HTTP on loopback, actual SQLite, no mocked application",
                      "external_destinations_followed": False, "checks": checks,
                      "sample": {"requests": 64, "concurrency": 8, "warmup_requests_excluded": 10,
                                 "mean_ms": round(statistics.mean(latencies), 3), "p50_ms": round(statistics.median(latencies), 3),
                                 "p95_ms": round(ordered[math.ceil(len(ordered) * 0.95) - 1], 3),
                                 "max_ms": round(max(latencies), 3), "elapsed_seconds": round(elapsed, 6),
                                 "requests_per_second": round(len(results) / elapsed, 3), "http_302": len(results)},
                      "measured_at_utc_epoch": time.time(), "expected_total": 74, "actual_total": stats["total_redirects"],
                      "limitations": ["Same-host short demonstration, not a production SLO", "Includes local HTTP and SQLite locking overhead",
                                      "One process, one link and eight clients", "No long-duration load, TLS, WAN, proxy or chaos infrastructure"]}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "evidence/validation/http-smoke.json")
    args = parser.parse_args()
    print(json.dumps(run(args.output), indent=2))
