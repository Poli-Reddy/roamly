"""Transparent, heuristic quality checks for live evaluation records.

These checks measure evidence coverage, not semantic truth. Results must be
reported as heuristic metrics and should be supplemented with human review.
"""

from __future__ import annotations

from collections.abc import Iterable


def _text(value: object) -> str:
    return str(value or "").casefold()


def _contains_any(text: str, terms: Iterable[str]) -> bool:
    return any(str(term).casefold() in text for term in terms if term)


def constraint_adherence(response: object, constraints: dict[str, list[str]]) -> dict[str, object]:
    """Check whether labeled constraint terms appear in the response."""
    response_text = _text(response)
    checks = {
        name: _contains_any(response_text, terms)
        for name, terms in constraints.items()
    }
    return {
        "checks": checks,
        "passed": sum(checks.values()),
        "total": len(checks),
        "rate": sum(checks.values()) / len(checks) if checks else None,
        "method": "case-insensitive term coverage; heuristic, not semantic proof",
    }


def evidence_coverage(response: object, evidence_terms: list[str]) -> dict[str, object]:
    """Check whether required tool-evidence terms are reflected in output."""
    response_text = _text(response)
    checks = {term: str(term).casefold() in response_text for term in evidence_terms}
    return {
        "checks": checks,
        "covered": sum(checks.values()),
        "total": len(checks),
        "rate": sum(checks.values()) / len(checks) if checks else None,
        "method": "case-insensitive evidence-term coverage; not hallucination detection",
    }
