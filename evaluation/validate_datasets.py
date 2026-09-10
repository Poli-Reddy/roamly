"""Validate evaluation dataset schemas without calling providers."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
KNOWN_AGENTS = {
    "flight_agent",
    "hotel_agent",
    "weather_agent",
    "budget_agent",
    "personalization_agent",
    "itinerary_agent",
    "safety_agent",
}


def _load(name: str, require_prompt: bool = True) -> list[dict]:
    path = ROOT / name
    cases = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(cases, list) or not cases:
        raise ValueError(f"{name}: expected a non-empty JSON array")
    ids = [case.get("id") for case in cases]
    if any(not item for item in ids) or len(ids) != len(set(ids)):
        raise ValueError(f"{name}: scenario IDs must be present and unique")
    if require_prompt and any(not isinstance(case.get("prompt"), str) or not case["prompt"].strip() for case in cases):
        raise ValueError(f"{name}: every prompt must be non-empty")
    return cases


def main() -> int:
    main_cases = _load("dataset.json")
    if len(main_cases) < 50:
        raise ValueError("dataset.json: expected at least 50 scenarios")

    routing = _load("routing_dataset.json")
    if len(routing) < 50:
        raise ValueError("routing_dataset.json: expected at least 50 scenarios")
    for case in routing:
        agents = case.get("expected_agents")
        if not isinstance(agents, list) or not agents or any(agent not in KNOWN_AGENTS for agent in agents):
            raise ValueError(f"{case['id']}: invalid expected_agents")
        if "itinerary_agent" not in agents:
            raise ValueError(f"{case['id']}: expected_agents must include itinerary_agent")

    quality = _load("quality_dataset.json")
    for case in quality:
        if not isinstance(case.get("constraints"), dict) or not case["constraints"]:
            raise ValueError(f"{case['id']}: structured constraints are required")
        if not isinstance(case.get("evidence_terms"), list):
            raise ValueError(f"{case['id']}: evidence_terms must be a list")

    security = _load("security_dataset.json")
    if any(not isinstance(case.get("expected_blocked"), bool) for case in security):
        raise ValueError("security_dataset.json: expected_blocked must be boolean")

    mcp_cases = _load("mcp_dataset.json", require_prompt=False)
    for case in mcp_cases:
        if not all(isinstance(case.get(key), str) and case[key] for key in ("server", "tool", "expected")):
            raise ValueError(f"{case.get('id', 'unknown')}: MCP cases need server, tool, and expected")
        if not isinstance(case.get("args"), dict):
            raise ValueError(f"{case['id']}: MCP args must be an object")
    for case in _load("hitl_dataset.json"):
        if case.get("action") not in {"approve", "reject"}:
            raise ValueError(f"{case['id']}: action must be approve or reject")
        if case["action"] == "reject" and not case.get("feedback", "").strip():
            raise ValueError(f"{case['id']}: rejected HITL cases need feedback")
        if not isinstance(case.get("required_changes"), dict):
            raise ValueError(f"{case['id']}: required_changes must be an object")

    print("dataset_validation=ok")
    print(f"main={len(main_cases)} routing={len(routing)} quality={len(quality)} security={len(security)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
