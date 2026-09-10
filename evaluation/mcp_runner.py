"""Live MCP evaluation using the configured external services, without mocks."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
DATASET_PATH = Path(__file__).with_name("mcp_dataset.json")
load_dotenv(ROOT / ".env")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    required = ("TAVILY_API_KEY", "AVIATION_STACK_API_KEY", "OPENWEATHER_API_KEY")
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        raise RuntimeError(f"MCP evaluation requires configured credentials: {', '.join(missing)}")

    sys.path.insert(0, str(ROOT))
    import mcp_client

    cases = json.loads(DATASET_PATH.read_text(encoding="utf-8"))
    records = []
    for case in cases:
        mcp_client.reset_tool_metrics()
        started = time.perf_counter()
        try:
            result = __import_tool(mcp_client, case["server"], case["tool"], case["args"])
            status = "success"
            error = None
            evidence = repr(result)[:4000]
        except Exception as exc:
            status = "failure"
            error = {
                "type": type(exc).__name__,
                "category": mcp_client._error_category(exc),
                "message": str(exc)[:500],
            }
            evidence = None
        records.append(
            {
                "id": case["id"],
                "server": case["server"],
                "tool": case["tool"],
                "expected": case["expected"],
                "status": status,
                "outcome_correct": (
                    status == "success"
                    if case["expected"] == "live_success"
                    else status == "failure"
                ),
                "error": error,
                "evidence": evidence,
                "latency_ms": round((time.perf_counter() - started) * 1000, 2),
                "tool_metrics": mcp_client.get_tool_metrics(),
            }
        )

    attempts = sum(record["tool_metrics"]["calls"] for record in records)
    successes = sum(record["tool_metrics"]["successes"] for record in records)
    expected_successes = [record for record in records if record["expected"] == "live_success"]
    expected_failures = [record for record in records if record["expected"] == "live_failure"]
    failure_categories = {}
    for record in records:
        for event in record["tool_metrics"].get("events", []):
            category = event.get("error_category")
            if category:
                failure_categories[category] = failure_categories.get(category, 0) + 1
        if record.get("error", {}).get("category"):
            category = record["error"]["category"]
            failure_categories[category] = failure_categories.get(category, 0) + 1
    summary = {
        "run_metadata": {
            "run_id": uuid.uuid4().hex,
            "mode": "live-mcp",
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "dataset_name": DATASET_PATH.name,
            "dataset_sha256": hashlib.sha256(DATASET_PATH.read_bytes()).hexdigest(),
            "git_sha": subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip() or None,
            "evaluation_method": "real MCP servers and configured external services; no mocks",
        },
        "metrics": {
            "scenario_count": len(records),
            "real_tool_attempts": attempts,
            "real_tool_successes": successes,
            "tool_success_rate": successes / attempts if attempts else None,
            "expected_success_count": len(expected_successes),
            "expected_success_pass_rate": (
                sum(record["outcome_correct"] for record in expected_successes) / len(expected_successes)
                if expected_successes else None
            ),
            "expected_failure_count": len(expected_failures),
            "expected_failure_handling_rate": (
                sum(record["outcome_correct"] for record in expected_failures) / len(expected_failures)
                if expected_failures else None
            ),
            "scenario_correctness_rate": sum(record["outcome_correct"] for record in records) / len(records),
            "safe_failure_count": sum(
                record["status"] == "failure" and record["expected"] == "live_failure"
                for record in records
            ),
            "unexpected_failure_count": sum(
                record["status"] == "failure" and record["expected"] == "live_success"
                for record in records
            ),
            "failure_categories": failure_categories,
        },
        "records": records,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.output.exists():
        raise FileExistsError(f"Refusing to overwrite evaluation evidence: {args.output}")
    args.output.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary["metrics"], sort_keys=True))
    return 0


def __import_tool(client, server: str, tool: str, args: dict):
    """Call only the fixed dataset allowlist; dataset values cannot choose arbitrary code."""
    allowed = {
        ("weather", "get_current_weather"): client.weather_mcp_search,
        ("weather", "get_forecast"): client.forecast_mcp_search,
        ("tavily", "tavily_search"): client.tavily_mcp_search,
        ("aviationstack", "list_airports"): lambda **_: client.aviation_mcp_call("list_airports"),
    }
    function = allowed.get((server, tool))
    if function is None:
        raise ValueError(f"Dataset tool is not allowlisted: {server}/{tool}")
    if server == "weather":
        return __import__("asyncio").run(function(args.get("city", "")))
    if server == "tavily":
        return __import__("asyncio").run(function(args.get("query", "")))
    return __import__("asyncio").run(function(**args))


if __name__ == "__main__":
    raise SystemExit(main())
