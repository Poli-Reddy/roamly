"""Live supervisor-routing evaluator using explicitly labeled agent sets."""

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
from typing import Any

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
DATASET_PATH = Path(__file__).with_name("routing_dataset.json")
load_dotenv(ROOT / ".env")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    if not os.getenv("GROQ_API_KEY") or not os.getenv("DATABASE_URL"):
        raise RuntimeError("Live routing evaluation requires GROQ_API_KEY and DATABASE_URL.")

    cases: list[dict[str, Any]] = json.loads(DATASET_PATH.read_text(encoding="utf-8"))
    selected_cases = cases[: args.limit] if args.limit else cases
    sys.path.insert(0, str(ROOT))
    from backend import run_travel_agent

    records = []
    for case in selected_cases:
        started = time.perf_counter()
        try:
            result = run_travel_agent(case["prompt"])
            actual = result.get("selected_agents", [])
            expected = case["expected_agents"]
            records.append(
                {
                    "id": case["id"],
                    "status": "ok",
                    "expected_agents": expected,
                    "actual_agents": actual,
                    "exact_match": actual == expected,
                    "latency_ms": round((time.perf_counter() - started) * 1000, 2),
                }
            )
        except Exception as exc:
            records.append(
                {
                    "id": case["id"],
                    "status": "error",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "exact_match": False,
                    "latency_ms": round((time.perf_counter() - started) * 1000, 2),
                }
            )

    matches = sum(record["exact_match"] for record in records)
    summary = {
        "run_metadata": {
            "run_id": uuid.uuid4().hex,
            "mode": "live",
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "scenario_count": len(records),
            "dataset_name": DATASET_PATH.name,
            "dataset_sha256": hashlib.sha256(DATASET_PATH.read_bytes()).hexdigest(),
            "git_sha": subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True
            ).stdout.strip() or None,
            "environment": os.getenv("ROAMLY_EVALUATION_ENV", "local"),
            "model": os.getenv("GROQ_MODEL", "openai/gpt-oss-20b"),
            "evaluation_method": "real LLM supervisor exact ordered selected-agent-set match",
        },
        "metrics": {
            "routing_accuracy": matches / len(records) if records else 0,
            "error_rate": sum(record["status"] == "error" for record in records) / len(records) if records else 0,
            "median_latency_ms": sorted(record["latency_ms"] for record in records)[len(records) // 2] if records else None,
        },
        "records": records,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.output.exists():
        raise FileExistsError(f"Refusing to overwrite evaluation evidence: {args.output}")
    args.output.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary["metrics"], sort_keys=True))
    print(f"Observed routing evaluation written to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
