"""Live PostgreSQL-backed HITL workflow evaluation."""

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
DATASET_PATH = Path(__file__).with_name("hitl_dataset.json")
load_dotenv(ROOT / ".env")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not os.getenv("GROQ_API_KEY") or not os.getenv("DATABASE_URL"):
        raise RuntimeError("HITL evaluation requires real GROQ_API_KEY and DATABASE_URL.")

    sys.path.insert(0, str(ROOT))
    from backend import resume_travel_agent, run_travel_agent
    from evaluation.quality import constraint_adherence

    cases = json.loads(DATASET_PATH.read_text(encoding="utf-8"))
    records = []
    for case in cases:
        thread_id = f"evaluation_hitl_{uuid.uuid4().hex}"
        started = time.perf_counter()
        try:
            draft = run_travel_agent(case["prompt"], thread_id=thread_id)
            resumed = resume_travel_agent(
                thread_id=thread_id,
                approved=case["action"] == "approve",
                feedback=case.get("feedback", ""),
            )
            adherence = constraint_adherence(
                resumed.get("answer"), case.get("required_changes", {})
            ) if case.get("required_changes") else None
            records.append(
                {
                    "id": case["id"],
                    "status": "ok",
                    "requires_approval": draft.get("requires_approval"),
                    "draft_thread_id": draft.get("thread_id"),
                    "resumed_thread_id": resumed.get("thread_id"),
                    "final_answer_present": bool(resumed.get("answer")),
                    "revision_requested": case["action"] == "reject",
                    "revision_in_progress": resumed.get("revision_in_progress", False),
                    "revision_adherence": adherence,
                    "latency_ms": round((time.perf_counter() - started) * 1000, 2),
                }
            )
        except Exception as exc:
            records.append(
                {
                    "id": case["id"],
                    "status": "error",
                    "error_type": type(exc).__name__,
                    "error": str(exc)[:500],
                    "latency_ms": round((time.perf_counter() - started) * 1000, 2),
                    "revision_adherence": None,
                }
            )

    completed = [record for record in records if record["status"] == "ok"]
    revision_records = [
        record for record in completed
        if record.get("revision_adherence") and record["revision_adherence"].get("rate") is not None
    ]
    summary = {
        "run_metadata": {
            "run_id": uuid.uuid4().hex,
            "mode": "live-hitl",
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "dataset_name": DATASET_PATH.name,
            "dataset_sha256": hashlib.sha256(DATASET_PATH.read_bytes()).hexdigest(),
            "git_sha": subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip() or None,
            "evaluation_method": "real LangGraph interrupt/resume with real PostgreSQL checkpointing and real LLM/MCP dependencies",
        },
        "metrics": {
            "scenario_count": len(records),
            "workflow_completion_rate": sum(record.get("final_answer_present", False) for record in completed) / len(records) if records else 0,
            "approval_interrupt_rate": sum(record.get("requires_approval", False) for record in completed) / len(records) if records else 0,
            "error_rate": sum(record["status"] == "error" for record in records) / len(records) if records else 0,
            "revision_adherence_heuristic_rate": (
                sum(record["revision_adherence"]["rate"] for record in revision_records) / len(revision_records)
                if revision_records else None
            ),
        },
        "records": records,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.output.exists():
        raise FileExistsError(f"Refusing to overwrite evaluation evidence: {args.output}")
    args.output.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary["metrics"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
