"""Rubric enforcement, beam selection and verdict rules (Week 4 section 2.3, Week 6).

The critic LLM answers the four rubric questions; this module re-checks the
parts that can be verified mechanically and applies the controller rules:

* timing: at least one *event* evidence item must have been public by the close.
* independence: support must come from >= 2 different source kinds, so ten
  articles about one 8-K still count once.
* price fit: company-specific explanations cannot fit a move that peers fully
  share, and sector/market explanations need the benchmarks to have moved.
* hallucinated evidence ids are dropped and recorded.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.analysis.timing import can_be_trigger
from app.models import CriticAssessment

COMPANY_CATEGORIES = {"earnings_guidance", "company_specific", "analyst_action", "corporate_action"}
PRIMARY_KINDS = {"sec_filing", "market_data", "macro_data", "computed", "earnings"}


@dataclass
class RubricResult:
    event: bool
    timing: bool
    price_fit: bool
    independent: bool
    decisive_contradiction: bool
    sources_conflict: bool
    total: int
    supporting_ids: list[str]
    contradicting_ids: list[str]
    event_ids: list[str]
    unknown_ids: list[str]
    notes: list[str]
    primary_sources: int

    def as_dict(self) -> dict:
        return self.__dict__.copy()


def score_branch(
    assessment: CriticAssessment,
    *,
    category: str,
    evidence: dict[str, dict],
    anomaly: dict,
) -> RubricResult:
    notes: list[str] = []

    def valid(ids: list[str]) -> list[str]:
        return [i for i in dict.fromkeys(ids) if i in evidence]

    all_cited = (
        assessment.event_evidence_ids
        + assessment.supporting_evidence_ids
        + assessment.contradicting_evidence_ids
    )
    unknown = sorted({i for i in all_cited if i not in evidence})
    if unknown:
        notes.append(f"Ignored citations to non-existent evidence: {', '.join(unknown)}.")

    event_ids = valid(assessment.event_evidence_ids)
    support_ids = valid(assessment.supporting_evidence_ids)
    contra_ids = valid(assessment.contradicting_evidence_ids)

    # 1. Event documented: critic says yes AND cites real evidence.
    event = assessment.event_documented and bool(event_ids)
    if assessment.event_documented and not event_ids:
        notes.append("Event check failed: no valid evidence documents the event.")

    # 2. Timing: deterministic. Credibility can't compensate for a timing failure.
    trigger_ids = [i for i in event_ids if can_be_trigger(evidence[i]["timing"])]
    timing = bool(trigger_ids)
    if event_ids and not timing:
        tl = {evidence[i]["timing"] for i in event_ids}
        notes.append(
            "Timing check failed: event evidence was "
            + "/".join(sorted(tl))
            + " the move, so it cannot be the public trigger."
        )

    # 3. Price fit: critic judgement with mechanical consistency guards.
    price_fit = assessment.price_fit
    scope = anomaly.get("scope")
    if category in COMPANY_CATEGORIES and scope in ("sector_wide", "market_wide"):
        if price_fit:
            notes.append(
                f"Price-fit overridden: the move was {scope.replace('_', ' ')}, which a "
                "company-specific cause alone does not fit."
            )
        price_fit = False
    if category == "sector_wide" and scope == "company_specific":
        if price_fit:
            notes.append("Price-fit overridden: sector benchmarks did not share the move.")
        price_fit = False
    if category == "market_wide" and scope in ("company_specific", "sector_wide"):
        if price_fit:
            notes.append("Price-fit overridden: the broad market did not share the move.")
        price_fit = False

    # 4. Independent support: different *kinds* of source.
    kinds = {evidence[i]["kind"] for i in set(support_ids) | set(event_ids)}
    independent = assessment.independent_support and len(kinds) >= 2
    if assessment.independent_support and len(kinds) < 2:
        notes.append("Independence check failed: all support comes from one type of source.")

    # Shared-move explanations are tested by prices, not documents: if the
    # benchmarks really moved with the stock, price fit and independent support
    # are established mechanically (each ticker is a separate dataset). The
    # sector's *catalyst* still needs a document for the event/timing checks.
    if category in ("sector_wide", "market_wide"):
        agree = shared_movers(anomaly, category)
        expected = "sector_wide" if category == "sector_wide" else "market_wide"
        if scope == expected and agree:
            if not price_fit:
                notes.append(f"Price fit confirmed by market data: {', '.join(agree)} moved with the stock.")
            price_fit = True
        if scope in ("sector_wide", "market_wide") and len(agree) >= 2:
            if not independent:
                notes.append(f"Independent support from separate price series: {', '.join(agree)}.")
            independent = True
            ids = [k for k, e in evidence.items() if e["kind"] == "market_data" and (e.get("meta") or {}).get("ticker") in agree]
            support_ids = list(dict.fromkeys(support_ids + ids))

    primary = sum(1 for i in set(support_ids) | set(event_ids) if evidence[i]["kind"] in PRIMARY_KINDS)
    total = int(event) + int(timing) + int(price_fit) + int(independent)
    return RubricResult(
        event=event,
        timing=timing,
        price_fit=price_fit,
        independent=independent,
        decisive_contradiction=assessment.decisive_contradiction,
        sources_conflict=assessment.sources_conflict,
        total=total,
        supporting_ids=support_ids,
        contradicting_ids=contra_ids,
        event_ids=event_ids,
        unknown_ids=unknown,
        notes=notes,
        primary_sources=primary,
    )


def shared_movers(anomaly: dict, category: str) -> list[str]:
    """Benchmarks that moved the same way as the stock by at least half as much."""
    ret = anomaly.get("ret") or 0
    roles = ("sector", "peer") if category == "sector_wide" else ("market",)
    return [
        b["ticker"] for b in anomaly.get("benchmarks", [])
        if b.get("role") in roles and b.get("ret") is not None and ret and b["ret"] / ret >= 0.5
    ]


def move_breakdown(anomaly: dict | None) -> dict | None:
    """How much of the move was shared with the market or sector (pure arithmetic)."""
    if not anomaly or not anomaly.get("ret"):
        return None
    ret, scope = anomaly["ret"], anomaly.get("scope")
    bm = {b["role"]: b for b in anomaly.get("benchmarks", []) if b.get("ret") is not None}
    ref = bm.get("market") if scope == "market_wide" else bm.get("sector") or bm.get("market")
    if not ref:
        return {"scope": scope, "ret": ret, "benchmark": None}
    share = max(0.0, min(1.0, ref["ret"] / ret))
    return {
        "scope": scope,
        "ret": ret,
        "benchmark": ref["ticker"],
        "benchmark_role": ref["role"],
        "benchmark_ret": ref["ret"],
        "shared_share": round(share, 3),
        "specific_ret": round(ret - ref["ret"], 5),
    }


def rank_key(branch: dict) -> tuple:
    """Higher score, then more independent primary sources, then lower branch number."""
    s = branch.get("score") or {}
    return (-s.get("total", 0), -s.get("primary_sources", 0), branch["order"])


def select_beam(
    branches: dict[str, dict], *, round_no: int, beam_width: int, prune_below: int
) -> tuple[list[str], dict[str, str]]:
    """Return (surviving ids, {pruned id: reason}) for active branches."""
    active = [b for b in branches.values() if b["status"] == "active"]
    pruned: dict[str, str] = {}
    survivors = []
    for b in active:
        s = b.get("score") or {}
        if s.get("decisive_contradiction"):
            pruned[b["id"]] = "ruled_out"
        elif s.get("total", 0) < prune_below:
            pruned[b["id"]] = f"score {s.get('total', 0)}/4 below {prune_below}/4"
        else:
            survivors.append(b)
    survivors.sort(key=rank_key)
    for b in survivors[beam_width:]:
        pruned[b["id"]] = f"outside beam width {beam_width}"
    return [b["id"] for b in survivors[:beam_width]], pruned


def qualifies_as_leading(branch: dict, min_score: int) -> tuple[bool, list[str]]:
    s = branch.get("score") or {}
    reasons = []
    if s.get("total", 0) < min_score:
        reasons.append(f"score {s.get('total', 0)}/4 is below {min_score}/4")
    if not s.get("event"):
        reasons.append("event not documented")
    if not s.get("timing"):
        reasons.append("timing check not passed")
    if not branch.get("challenge_completed"):
        reasons.append("final challenge not completed")
    if s.get("decisive_contradiction"):
        reasons.append("decisively contradicted")
    return (not reasons), reasons


def decide_verdict(branches: dict[str, dict], min_score: int, anomaly: dict | None = None) -> dict:
    """Rank explanations and decide the verdict. Ties share a rank.

    * leading: an explanation passed every check.
    * needs_review: it did, but credible sources conflict.
    * shared_move: nothing passed, but prices prove most of the move was
      sector- or market-wide - an established finding in its own right.
    * unresolved: the move is real but no cause is established.
    """
    ranked = sorted(
        [b for b in branches.values() if b["status"] != "ruled_out"], key=rank_key
    )
    qualifying = []
    blockers: dict[str, list[str]] = {}
    for b in ranked:
        ok, why = qualifies_as_leading(b, min_score)
        if ok:
            qualifying.append(b)
        else:
            blockers[b["id"]] = why

    leading: list[str] = []
    if qualifying:
        top = qualifying[0]["score"]["total"]
        leading = [b["id"] for b in qualifying if b["score"]["total"] == top]

    needs_review = any(
        (branches[i].get("score") or {}).get("sources_conflict") for i in leading
    )
    # Ranks: equal (total, primary_sources) share a rank.
    ranks: dict[str, int] = {}
    prev_key, rank = None, 0
    for idx, b in enumerate(ranked, start=1):
        k = rank_key(b)[:2]
        if k != prev_key:
            rank, prev_key = idx, k
        ranks[b["id"]] = rank

    status = "leading" if leading and not needs_review else ("needs_review" if leading else "unresolved")
    breakdown = move_breakdown(anomaly)
    if status == "unresolved" and breakdown and breakdown["scope"] in ("sector_wide", "market_wide"):
        status = "shared_move"
    return {
        "status": status,
        "breakdown": breakdown,
        "leading_ids": leading if status == "leading" else [],
        "withheld_ids": leading if status == "needs_review" else [],
        "ranks": ranks,
        "blockers": blockers,
    }
