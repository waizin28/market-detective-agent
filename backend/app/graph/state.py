"""LangGraph state for one investigation (short-term memory, Week 2 section 2).

Values are plain JSON-friendly dicts so the SQLite checkpointer can persist
every step and a case can resume after a crash or a human-in-the-loop pause.
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict


def merge_dicts(left: dict | None, right: dict | None) -> dict:
    """Reducer: shallow-merge keyed records (evidence by id, branches by id)."""
    out = dict(left or {})
    out.update(right or {})
    return out


class CaseState(TypedDict, total=False):
    # --- request ---------------------------------------------------------
    case_id: str
    query: str
    requested_date: str | None
    peers_override: list[str] | None
    force: bool

    # --- resolved security ------------------------------------------------
    ticker: str
    company: str
    short_name: str
    cik: int | None
    profile: dict
    sector_etf: str | None
    peers: list[str]

    # --- detection -------------------------------------------------------
    anomaly: dict | None
    prices: list[dict]
    benchmark_prices: dict[str, list[dict]]

    # --- investigation memory ------------------------------------------------
    evidence: Annotated[dict[str, dict], merge_dicts]
    branches: Annotated[dict[str, dict], merge_dicts]
    findings: Annotated[list[dict], operator.add]
    gaps: Annotated[list[str], operator.add]
    events: Annotated[list[dict], operator.add]  # audit trail shown in the UI
    pending_checks: list[dict]
    round: int
    checks_used: Annotated[int, operator.add]

    # --- output ----------------------------------------------------------
    report: dict | None
    status: str
    error: str | None


class ResearchTask(TypedDict):
    """Payload sent to each parallel ``research_check`` worker via ``Send``."""

    check: dict
    branch: dict
    ticker: str
    company: str
    cik: int | None
    anomaly: dict
    evidence: dict[str, dict]
    round: int


def audit(role: str, message: str, **extra: Any) -> dict:
    from app.agents.evidence import now_iso

    return {"at": now_iso(), "role": role, "message": message, **extra}
