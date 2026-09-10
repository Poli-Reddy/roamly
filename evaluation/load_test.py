"""Small HTTP concurrency probe for a running Roamly deployment."""

from __future__ import annotations

import argparse
import json
import statistics
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8000/api/travel")
    parser.add_argument("--requests", type=int, default=10)
    parser.add_argument("--concurrency", type=int, default=5)
    parser.add_argument("--api-key", default="")
    args = parser.parse_args()

    headers = {"Content-Type": "application/json"}
    if args.api_key:
        headers["X-API-Key"] = args.api_key

    def send(index: int) -> dict[str, object]:
        started = time.perf_counter()
        try:
            response = requests.post(
                args.url,
                headers=headers,
                json={"message": f"Plan a short trip to Paris, probe request {index}."},
                timeout=120,
            )
            return {
                "status": response.status_code,
                "latency_ms": round((time.perf_counter() - started) * 1000, 2),
            }
        except Exception as exc:
            return {
                "status": "error",
                "error_type": type(exc).__name__,
                "latency_ms": round((time.perf_counter() - started) * 1000, 2),
            }

    with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
        records = list(executor.map(send, range(args.requests)))

    latencies = sorted(record["latency_ms"] for record in records)
    successful = sum(record["status"] == 200 for record in records)
    result = {
        "url": args.url,
        "requests": args.requests,
        "concurrency": args.concurrency,
        "success_rate": successful / len(records) if records else 0,
        "p50_latency_ms": statistics.median(latencies) if latencies else None,
        "p95_latency_ms": latencies[max(0, int(len(latencies) * 0.95) - 1)] if latencies else None,
        "status_counts": {str(status): sum(record["status"] == status for record in records) for status in set(record["status"] for record in records)},
        "records": records,
    }
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
