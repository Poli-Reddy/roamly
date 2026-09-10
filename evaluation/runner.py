"""Live evaluation runner for Roamly.

This runner intentionally does not synthesize scores. It invokes the configured
agent and writes observed outcomes. Use --mode live only with real credentials.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
DATASET_PATH = Path(__file__).with_name("dataset.json")
RESULTS_DIR = Path(__file__).with_name("results")
load_dotenv(ROOT / ".env")


def _git_sha() -> str | None:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip() or None
    except (OSError, subprocess.CalledProcessError):
        return None


def load_dataset(dataset_path: Path = DATASET_PATH) -> list[dict[str, Any]]:
    cases = json.loads(dataset_path.read_text(encoding="utf-8"))
    if not isinstance(cases, list) or len(cases) < 1:
        raise ValueError("The evaluation dataset must contain at least one scenario.")
    required = {"id", "prompt"}
    if any(not required.issubset(case) for case in cases):
        raise ValueError("Every scenario needs id and prompt.")
    return cases


def _structured_output_ok(result: dict[str, Any]) -> bool:
    return isinstance(result.get("selected_agents"), list) and isinstance(
        result.get("trip_constraints"), dict
    )


def _task_completed(result: dict[str, Any], expected_allowed: bool) -> bool:
    if expected_allowed:
        return bool(result.get("answer")) and bool(result.get("itinerary"))
    return result.get("guardrail_allowed") is False and bool(result.get("answer"))


def run_live(
    cases: list[dict[str, Any]],
    limit: int | None,
    dataset_path: Path = DATASET_PATH,
) -> dict[str, Any]:
    if not os.getenv("GROQ_API_KEY") or not os.getenv("DATABASE_URL"):
        raise RuntimeError(
            "Live evaluation requires real GROQ_API_KEY and DATABASE_URL. "
            "No result was generated because credentials are missing."
        )

    sys.path.insert(0, str(ROOT))
    from backend import run_travel_agent
    from mcp_client import get_tool_metrics, reset_tool_metrics
    from evaluation.quality import constraint_adherence, evidence_coverage

    selected_cases = cases[:limit] if limit else cases
    records: list[dict[str, Any]] = []
    for case in selected_cases:
        reset_tool_metrics()
        started = time.perf_counter()
        record: dict[str, Any] = {
            "id": case["id"],
            "category": case.get("category", "quality"),
            "expected_guardrail": case.get("expected_guardrail", True),
        }
        try:
            result = run_travel_agent(case["prompt"])
            expected_guardrail = case.get("expected_guardrail", True)
            quality = None
            if case.get("constraints") or case.get("evidence_terms"):
                quality = {
                    "constraints": constraint_adherence(
                        result.get("answer"), case.get("constraints", {})
                    ) if case.get("constraints") else None,
                    "evidence": evidence_coverage(
                        result.get("answer"), case.get("evidence_terms", [])
                    ) if case.get("evidence_terms") else None,
                }
            record.update(
                {
                    "status": "ok",
                    "task_completed": _task_completed(result, expected_guardrail),
                    "guardrail_correct": result.get("guardrail_allowed") == expected_guardrail,
                    "structured_output": _structured_output_ok(result),
                    "hallucination_flag": None,
                    "llm_calls": result.get("llm_calls"),
                    "quality": quality,
                }
            )
        except Exception as exc:
            record.update(
                {
                    "status": "error",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "task_completed": False,
                    "guardrail_correct": False,
                    "structured_output": False,
                    "hallucination_flag": None,
                    "llm_calls": None,
                    "quality": None,
                }
            )
        record["latency_ms"] = round((time.perf_counter() - started) * 1000, 2)
        record["tool_metrics"] = get_tool_metrics()
        records.append(record)
        print(json.dumps(record, sort_keys=True), flush=True)

    completed = [record for record in records if record["status"] == "ok"]
    tool_calls = sum(record["tool_metrics"]["calls"] for record in records)
    tool_successes = sum(record["tool_metrics"]["successes"] for record in records)
    quality_records = [record["quality"] for record in records if record.get("quality")]
    blocked_cases = [
        record for record, case in zip(records, selected_cases)
        if not case.get("expected_guardrail", True)
    ]
    summary = {
        "run_metadata": {
            "run_id": uuid.uuid4().hex,
            "mode": "live",
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "scenario_count": len(records),
            "dataset_path": str(dataset_path),
            "dataset_sha256": hashlib.sha256(dataset_path.read_bytes()).hexdigest(),
            "git_sha": _git_sha(),
            "environment": os.getenv("ROAMLY_EVALUATION_ENV", "local"),
            "model": os.getenv("GROQ_MODEL", "openai/gpt-oss-20b"),
            "evaluation_method": "real LLM, real PostgreSQL checkpointing, and real MCP calls where routed",
            "cost_per_request": "unknown: provider usage pricing is not configured",
            "hallucination_rate": "not_claimed: automated semantic verification is not implemented",
        },
        "metrics": {
            "task_completion_rate": sum(r["task_completed"] for r in completed) / len(records) if records else 0,
            "tool_call_success_rate": tool_successes / tool_calls if tool_calls else None,
            "structured_output_success_rate": sum(r["structured_output"] for r in completed) / len(records) if records else 0,
            "guardrail_accuracy": sum(r["guardrail_correct"] for r in completed) / len(records) if records else 0,
            "guardrail_blocking_rate": sum(
                record.get("guardrail_correct") is True
                and record.get("status") == "ok"
                for record in blocked_cases
            ) / len(blocked_cases) if blocked_cases else None,
            "median_latency_ms": sorted(r["latency_ms"] for r in records)[len(records) // 2] if records else None,
            "error_rate": sum(r["status"] == "error" for r in records) / len(records) if records else 0,
            "constraint_adherence_rate": (
                sum(item["constraints"]["rate"] for item in quality_records if item.get("constraints") and item["constraints"]["rate"] is not None)
                / sum(bool(item.get("constraints") and item["constraints"]["rate"] is not None) for item in quality_records)
                if any(item.get("constraints") and item["constraints"]["rate"] is not None for item in quality_records)
                else None
            ),
            "evidence_coverage_rate": (
                sum(item["evidence"]["rate"] for item in quality_records if item.get("evidence") and item["evidence"]["rate"] is not None)
                / sum(bool(item.get("evidence") and item["evidence"]["rate"] is not None) for item in quality_records)
                if any(item.get("evidence") and item["evidence"]["rate"] is not None for item in quality_records)
                else None
            ),
        },
        "category_counts": dict(Counter(case.get("category", "quality") for case in selected_cases)),
        "records": records,
    }
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("live",), default="live")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--dataset", type=Path, default=DATASET_PATH)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    cases = load_dataset(args.dataset)
    summary = run_live(cases, args.limit, args.dataset)
    output_path = args.output or RESULTS_DIR / f"run-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists():
        raise FileExistsError(
            f"Refusing to overwrite evaluation evidence: {output_path}. "
            "Choose a new output path."
        )
    output_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"Observed evaluation written to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
