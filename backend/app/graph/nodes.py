"""Graph nodes. Each node is one step of the Week 4/5 workflow:

resolve_case -> detect_anomaly -> build_timeline -> propose_hypotheses
  -> research_check (parallel, via Send) -> critique -> controller
  -> refine_branches | plan_challenges -> research_check ... -> write_report
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import date, datetime, timedelta

from langgraph.config import get_stream_writer
from langgraph.runtime import Runtime
from langgraph.types import Send, interrupt

from app.agents.evidence import CaseContext, Ledger, evidence_id, in_news_window, now_iso, price_history_url
from app.analysis.anomaly import compute_anomaly, price_points
from app.analysis.scoring import decide_verdict, rank_key, score_branch, select_beam
from app.analysis.timing import market_close
from app.config import get_settings
from app.data.http import BudgetExceeded, DataUnavailable
from app.data.market import sector_etf_for
from app.graph.deps import Deps
from app.graph.state import CaseState, ResearchTask, audit
from app.models import Anomaly, Evidence, ReportNarrative

log = logging.getLogger(__name__)

DISCLAIMER = (
    "Research output, not investment advice. Rubric scores measure evidence conditions, "
    "not the probability that an explanation caused the move. Co-occurring events are not "
    "proof of causation."
)


def _emit(role: str, message: str, **extra) -> dict:
    """Stream a progress event to the UI and return it for the audit trail."""
    ev = audit(role, message, **extra)
    try:
        get_stream_writer()({"type": "progress", **ev})
    except Exception:  # not running under a stream
        pass
    return ev


def _short_name(name: str) -> str:
    n = re.sub(r"[,\.]?\s+(inc|corp|corporation|co|company|ltd|plc|holdings|group|class [a-z])\.?$", "", name, flags=re.I)
    return n.strip() or name


# ===========================================================================
# 1. Coordinator: confirm the security (human clarification when ambiguous)
# ===========================================================================
async def resolve_case(state: CaseState, runtime: Runtime[Deps]) -> dict:
    deps = runtime.context
    query = state["query"].strip()
    events = [_emit("coordinator", f"Resolving '{query}'")]
    matches = await deps.sec.resolve(query)

    choice = None
    if len(matches) == 1:
        choice = matches[0]
    else:
        # Ambiguous or unknown: pause for a human (Week 6 section 4). The UI
        # shows the candidates; resuming with a ticker continues the case.
        answer = interrupt(
            {
                "type": "clarify_security",
                "question": (
                    f"'{query}' matches several companies. Which one should be investigated?"
                    if matches
                    else f"Couldn't find '{query}' in SEC EDGAR. Enter the exact ticker (ETFs are allowed)."
                ),
                "candidates": [{"ticker": c.ticker, "name": c.title} for c in matches],
            }
        )
        picked = str(answer).strip().upper()
        resolved = await deps.sec.resolve(picked)
        choice = resolved[0] if resolved and resolved[0].ticker == picked else None
        ticker = picked
        if choice is None:
            events.append(_emit("coordinator", f"{ticker} has no SEC CIK; continuing with market data only"))

    ticker = choice.ticker if choice else ticker
    profile = await deps.market.profile(ticker)
    name = profile.name or (choice.title if choice else ticker)
    sector_etf = sector_etf_for(profile)

    peers = [p.upper() for p in (state.get("peers_override") or [])][:3]
    if not peers:
        try:
            plan = await deps.roles.propose_peers(name, ticker, profile.sector, profile.industry)
            peers = [p.upper() for p in plan.peers if p.upper() != ticker][:3]
            events.append(_emit("coordinator", f"Peers selected: {', '.join(peers)} - {plan.rationale}"))
        except Exception as exc:  # peers are helpful, not required
            log.warning("peer selection failed: %s", exc)
            events.append(_emit("coordinator", "Peer selection unavailable; using market and sector benchmarks only"))
    events.append(
        _emit("coordinator", f"Investigating {name} ({ticker}); sector benchmark {sector_etf or 'none'}")
    )
    return {
        "ticker": ticker,
        "company": name,
        "short_name": _short_name(name),
        "cik": choice.cik if choice else None,
        "profile": profile.__dict__,
        "sector_etf": sector_etf,
        "peers": peers,
        "status": "detecting",
        "events": events,
    }


# ===========================================================================
# 2. Detect the unusual move (deterministic Python)
# ===========================================================================
async def detect_anomaly(state: CaseState, runtime: Runtime[Deps]) -> dict:
    deps, s = runtime.context, runtime.context.settings
    ticker = state["ticker"]
    req = date.fromisoformat(state["requested_date"]) if state.get("requested_date") else None
    end = req + timedelta(days=3) if req else date.today()
    end = min(end, date.today())
    # ~1.6 calendar days per trading day, plus the scan window, plus slack.
    start = end - timedelta(days=int((s.anomaly_lookback_days + s.anomaly_scan_days) * 1.6) + 200)

    df = await deps.market.history(ticker, start, end)
    bench_specs = {"SPY": "market"}
    if state.get("sector_etf"):
        bench_specs[state["sector_etf"]] = "sector"
    for p in state.get("peers", []):
        bench_specs.setdefault(p, "peer")

    async def fetch(t):
        try:
            return t, await deps.market.history(t, start, end)
        except (DataUnavailable, BudgetExceeded):
            return t, None

    fetched = dict(await asyncio.gather(*(fetch(t) for t in bench_specs)))
    benchmarks = {t: (role, fetched[t]) for t, role in bench_specs.items()}
    a: Anomaly = compute_anomaly(
        ticker,
        df,
        benchmarks,
        lookback=s.anomaly_lookback_days,
        z_threshold=s.anomaly_z_threshold,
        scan_days=s.anomaly_scan_days,
        requested_date=req,
    )
    msg = (
        f"{ticker} moved {a.ret:+.2%} on {a.event_date} (z = {a.z_score:+.2f}, "
        f"{'FLAGGED' if a.flagged else 'within normal range'}; scope: {a.scope.replace('_', ' ')})"
    )
    return {
        "anomaly": a.model_dump(),
        "prices": [p.model_dump() for p in price_points(df)],
        "benchmark_prices": {
            t: [p.model_dump() for p in price_points(d)] for t, d in fetched.items() if d is not None
        },
        "gaps": a.data_issues,
        "status": "investigating" if (a.flagged or state.get("force")) else "no_anomaly",
        "events": [_emit("detector", msg, data_source=df.attrs.get("source"))],
    }


def route_after_timeline(state: CaseState) -> str:
    return "propose_hypotheses" if state["status"] == "investigating" else "no_anomaly_report"


def _normal_day_summary(state: dict, th: float) -> str:
    a = state["anomaly"]
    bm = "; ".join(
        f"{b['ticker']} ({b['role']}) {b['ret']:+.2%}" for b in a["benchmarks"] if b.get("ret") is not None
    )
    news = sum(1 for e in state["evidence"].values() if e["kind"] == "news" and e["timing"] in ("before", "during"))
    return (
        f"{state['ticker']} moved {a['ret']:+.2%} on {a['event_date']}, a z-score of {a['z_score']:+.2f} "
        f"against the prior {a['lookback_days']} trading days - inside the normal ±{th:.1f} range, so the "
        f"full hypothesis investigation was not run. Same day: {bm or 'no benchmark data'}. "
        f"{news} news items about the company from the preceding week are in the timeline."
    )


async def no_anomaly_report(state: CaseState, runtime: Runtime[Deps]) -> dict:
    """Normal-range move: still give the reader the day's numbers, the week's news and,
    when enabled, a single-call coordinator brief of what the news says."""
    deps, s = runtime.context, runtime.context.settings
    a = state["anomaly"]
    th = s.anomaly_z_threshold
    headline = f"{state['ticker']} {a['ret']:+.2%} on {a['event_date']}: within its normal range"
    why, summary, key_events, uncertainties, next_steps = "", _normal_day_summary(state, th), [], [], []
    if s.brief_normal_days:
        try:
            n = await deps.roles.brief_normal_day(state)
            ev = state["evidence"]
            ev_day = date.fromisoformat(a["event_date"])
            headline, why, uncertainties, next_steps = n.headline, n.why_it_moved, n.uncertainties, n.next_steps
            summary = n.summary or summary
            key_events = sorted(
                (
                    {"evidence_id": k.evidence_id, "headline": k.headline, "brief": k.brief}
                    for k in {k.evidence_id: k for k in n.key_events}.values()
                    if k.evidence_id in ev
                    and ev[k.evidence_id]["kind"] in ("news", "sec_filing")
                    and ev[k.evidence_id]["timing"] in ("before", "during")
                    and in_news_window(ev[k.evidence_id], ev_day, 7)
                ),
                key=lambda k: _published_ts(ev[k["evidence_id"]]),
            )
        except Exception:
            log.exception("normal-day brief failed; using the computed summary")
    report = {
        "headline": headline,
        "why_it_moved": why,
        "summary": summary,
        "key_events": key_events,
        "verdict": {"status": "no_anomaly", "leading_ids": [], "ranks": {}, "blockers": {}},
        "explanations": [],
        "uncertainties": uncertainties,
        "next_steps": next_steps,
        "disclaimer": DISCLAIMER,
        "generated_at": now_iso(),
    }
    return {"report": report, "status": "complete", "events": [_emit("coordinator", headline)]}


# ===========================================================================
# 3. Initial timeline (researcher, deterministic retrieval)
# ===========================================================================
def _case_ctx(state: dict, deps: Deps) -> CaseContext:
    a = state["anomaly"]
    return CaseContext(
        ticker=state["ticker"],
        company=state["company"],
        cik=state.get("cik"),
        event_date=date.fromisoformat(a["event_date"]),
        prev_date=date.fromisoformat(a["prev_date"]),
        deps=deps,
    )


async def build_timeline(state: CaseState, runtime: Runtime[Deps]) -> dict:
    deps, s = runtime.context, runtime.context.settings
    a = state["anomaly"]
    ledger = Ledger(_case_ctx(state, deps))
    close_iso = market_close(date.fromisoformat(a["event_date"])).isoformat()

    # The move itself and same-day benchmark returns are computed evidence.
    ledger.add(
        Evidence(
            id=evidence_id("anomaly", state["ticker"], a["event_date"]),
            kind="computed",
            title=f"{state['ticker']} {a['ret']:+.2%} on {a['event_date']} (z {a['z_score']:+.2f})",
            source="Market Detective anomaly detector",
            url=price_history_url(state["ticker"]),
            published_at=close_iso,
            retrieved_at=now_iso(),
            timing="during",
            snippet=(
                f"Close {a['prev_close']:.2f} -> {a['close']:.2f}; mean daily return {a['mean_ret']:+.2%}, "
                f"sd {a['std_ret']:.2%} over {a['lookback_days']} days; scope {a['scope']}."
            ),
        )
    )
    for b in a["benchmarks"]:
        if b["ret"] is None:
            continue
        ledger.add(
            Evidence(
                id=evidence_id("ret", b["ticker"], a["event_date"]),
                kind="market_data",
                title=f"{b['ticker']} ({b['role']}) daily return on {a['event_date']}: {b['ret']:+.2%}",
                source="market data",
                url=price_history_url(b["ticker"]),
                published_at=close_iso,
                retrieved_at=now_iso(),
                timing="during",
                snippet=f"{b['ticker']} {b['ret']:+.2%} vs {state['ticker']} {a['ret']:+.2%}.",
                meta={"ticker": b["ticker"], "ret": b["ret"], "role": b["role"]},
            )
        )

    query = f'"{state["short_name"]}" OR {state["ticker"]}'
    tasks = {
        "filings": ledger.filings(s.filings_lookback_days, 2),
        # Same-day market coverage ("why X stock fell") is usually the most
        # direct record of what traders were reacting to.
        "move_news": ledger.news(f'"{state["short_name"]}" stock', 1, 1, limit=12),
    }
    # Wide timeline: search the lead-up week by week so older developments
    # (earnings, leadership changes, regulatory news) aren't crowded out by
    # the most recent headlines.
    for wk, start in enumerate(range(7, s.news_lookback_days + 1, 7)):
        tasks[f"news_week_{wk}"] = ledger.news(query, start, -(start - 7) + (1 if start == 7 else 0), limit=10)
    if deps.fred.available:
        tasks["macro"] = ledger.macro()
    results = await asyncio.gather(*tasks.values(), return_exceptions=True)
    gaps = list(ledger.gaps)
    for name, res in zip(tasks, results):
        if isinstance(res, Exception):
            gaps.append(f"Initial {name} retrieval unavailable: {res}")
    if not deps.fred.available:
        gaps.append("Macro data (FRED) not configured; macro explanations can't be checked against yields.")

    counts = {k: sum(1 for e in ledger.items.values() if e["kind"] == k) for k in ("sec_filing", "news", "macro_data")}
    after = sum(1 for e in ledger.items.values() if e["timing"] == "after")
    msg = (
        f"Timeline built: {counts['sec_filing']} filings, {counts['news']} articles, "
        f"{counts['macro_data']} macro items ({after} published after the move)"
    )
    return {"evidence": ledger.items, "gaps": gaps, "events": [_emit("researcher", msg)]}


# ===========================================================================
# 4. Analyst: propose competing hypotheses (ToT depth 1)
# ===========================================================================
async def propose_hypotheses(state: CaseState, runtime: Runtime[Deps]) -> dict:
    deps, s = runtime.context, runtime.context.settings
    proposal = await deps.roles.propose_hypotheses(state, s.max_initial_hypotheses)
    branches, checks = {}, []
    for i, h in enumerate(proposal.hypotheses[: s.max_initial_hypotheses], start=1):
        bid = f"H{i}"
        branches[bid] = {
            "id": bid,
            "order": i,
            "title": h.title,
            "category": h.category,
            "statement": h.statement,
            "prediction": h.prediction,
            "would_weaken": h.would_weaken,
            "status": "active",
            "depth": 1,
            "score": None,
            "score_history": [],
            "refinements": [],
            "challenge_completed": False,
            "notes": [],
        }
        checks.append({**h.initial_check.model_dump(), "purpose": "initial", "branch_id": bid})
    titles = "; ".join(f"{b['id']} {b['title']}" for b in branches.values())
    return {
        "branches": branches,
        "pending_checks": checks,
        "round": 1,
        "events": [_emit("analyst", f"Round 1 - proposed {len(branches)} hypotheses: {titles}")],
    }


# ===========================================================================
# 5. Researcher: run evidence checks in parallel
# ===========================================================================
def dispatch_checks(state: CaseState) -> list[Send] | str:
    s = get_settings()
    remaining = s.max_evidence_checks - (state.get("checks_used") or 0)
    checks = (state.get("pending_checks") or [])[: max(0, remaining)]
    if not checks:
        return "write_report"
    return [
        Send(
            "research_check",
            ResearchTask(
                check=c,
                branch=state["branches"][c["branch_id"]],
                ticker=state["ticker"],
                company=state["company"],
                cik=state.get("cik"),
                anomaly=state["anomaly"],
                evidence=state["evidence"],
                round=state["round"],
            ),
        )
        for c in checks
    ]


async def research_check(task: ResearchTask, runtime: Runtime[Deps]) -> dict:
    deps = runtime.context
    branch, check = task["branch"], task["check"]
    ledger = Ledger(_case_ctx(task, deps), existing=task["evidence"])
    status, outcome = "completed", None
    try:
        finding = await deps.roles.research(task, branch, check, ledger)
        outcome = finding.model_dump()
    except Exception as exc:  # an interrupted/failed check is never recorded as completed
        log.exception("research check failed")
        status = "failed"
        outcome = {"summary": f"Check failed: {exc}", "outcome": "insufficient",
                   "supporting_evidence_ids": [], "contradicting_evidence_ids": []}
    record = {
        "branch_id": branch["id"],
        "round": task["round"],
        "purpose": check["purpose"],
        "question": check["question"],
        "status": status,
        "new_evidence_ids": list(ledger.items),
        **outcome,
    }
    msg = f"{branch['id']} {check['purpose']} check: {outcome['outcome']} ({len(ledger.items)} new evidence items)"
    return {
        "evidence": ledger.items,
        "findings": [record],
        "gaps": ledger.gaps,
        "checks_used": 1,
        "events": [_emit("researcher", msg, branch_id=branch["id"])],
    }


# ===========================================================================
# 6. Critic: score each active branch; Python re-verifies the rubric
# ===========================================================================
async def critique(state: CaseState, runtime: Runtime[Deps]) -> dict:
    deps = runtime.context
    rnd = state["round"]
    active = [b for b in state["branches"].values() if b["status"] == "active"]
    batch = await deps.roles.critique(state, active)
    by_id = {a.branch_id: a for a in batch.assessments}
    updates, events = {}, []
    for b in active:
        a = by_id.get(b["id"])
        nb = dict(b)
        if a is None:
            nb["notes"] = b["notes"] + [f"Round {rnd}: critic returned no assessment."]
            nb["score"] = nb["score"] or {"total": 0, "event": False, "timing": False, "price_fit": False,
                                          "independent": False, "decisive_contradiction": False,
                                          "sources_conflict": False, "primary_sources": 0}
        else:
            r = score_branch(a, category=b["category"], evidence=state["evidence"], anomaly=state["anomaly"])
            nb["score"] = r.as_dict()
            nb["critic_rationale"] = a.rationale
            nb["notes"] = b["notes"] + [f"Round {rnd}: {n}" for n in r.notes]
        nb["score_history"] = b["score_history"] + [{"round": rnd, "total": nb["score"]["total"]}]
        if rnd == 3:
            # Challenge counts only if this branch's challenge check actually completed.
            nb["challenge_completed"] = any(
                f["branch_id"] == b["id"] and f["purpose"] == "challenge" and f["status"] == "completed"
                for f in state.get("findings", [])
            )
        updates[b["id"]] = nb
        events.append(_emit("critic", f"{b['id']} {b['title']}: {nb['score']['total']}/4", branch_id=b["id"]))
    return {"branches": updates, "events": events}


# ===========================================================================
# 7. Controller: beam search, pruning and stopping rules (never the LLM)
# ===========================================================================
async def controller(state: CaseState, runtime: Runtime[Deps]) -> dict:
    s = runtime.context.settings
    rnd = state["round"]
    keep, pruned = select_beam(
        state["branches"], round_no=rnd, beam_width=s.beam_width, prune_below=s.prune_below_score
    )
    updates = {}
    # If every branch fell below the pruning bar only because evidence was
    # *missing* (not contradicted), keep the best ones provisionally so the
    # refine round can look again with sharper checks.
    provisional: list[str] = []
    if not keep and rnd < s.max_rounds:
        candidates = sorted(
            (state["branches"][i] for i, r in pruned.items() if r != "ruled_out"), key=rank_key
        )
        provisional = [b["id"] for b in candidates[: s.beam_width]]
        for bid in provisional:
            pruned.pop(bid)
            b = dict(state["branches"][bid])
            b["notes"] = b["notes"] + [
                f"Round {rnd}: kept provisionally - low score reflects missing evidence, not contradiction"
            ]
            updates[bid] = b
        keep = provisional
    for bid, reason in pruned.items():
        b = dict(state["branches"][bid])
        b["status"] = "ruled_out" if reason == "ruled_out" else "pruned"
        b["notes"] = b["notes"] + [f"Round {rnd}: pruned ({reason})"]
        updates[bid] = b
    msg = f"Round {rnd} complete - keeping {', '.join(keep) or 'none'}"
    if pruned:
        msg += "; pruned " + ", ".join(f"{k} ({v})" for k, v in pruned.items())
    return {"branches": updates, "pending_checks": [], "events": [_emit("controller", msg)]}


def route_after_controller(state: CaseState) -> str:
    s = get_settings()
    active = [b for b in state["branches"].values() if b["status"] == "active"]
    used = state.get("checks_used") or 0
    if not active or used >= s.max_evidence_checks or state["round"] >= s.max_rounds:
        return "write_report"
    if state["round"] == 1:
        return "refine_branches"
    if s.skip_hopeless_challenges and not any(_could_lead(b) for b in active):
        return "write_report"
    return "plan_challenges"


def _could_lead(b: dict) -> bool:
    """A challenge can only confirm or knock down a branch; it can't supply the
    documented event or pre-close timing that a leading explanation needs."""
    sc = b.get("score") or {}
    return bool(sc.get("event") and sc.get("timing"))


async def refine_branches(state: CaseState, runtime: Runtime[Deps]) -> dict:
    deps, s = runtime.context, runtime.context.settings
    active = [b for b in state["branches"].values() if b["status"] == "active"]
    plan = await deps.roles.refine(state, active, s.max_refinements_per_branch)
    updates, checks = {}, []
    budget = s.max_evidence_checks - (state.get("checks_used") or 0)
    # Reserve one final challenge per surviving branch.
    budget -= len(active)
    for r in plan.refinements:
        b = state["branches"].get(r.branch_id)
        if not b or b["status"] != "active":
            continue
        nb = dict(b)
        nb["refinements"] = b["refinements"] + [r.refined_statement]
        nb["statement"] = r.refined_statement
        nb["depth"] = 2
        updates[b["id"]] = nb
        for c in r.checks[: s.max_refinements_per_branch]:
            if len(checks) < budget:
                checks.append({**c.model_dump(), "purpose": "refine", "branch_id": b["id"]})
    return {
        "branches": updates,
        "pending_checks": checks,
        "round": 2,
        "events": [_emit("analyst", f"Round 2 - refined {len(updates)} branches with {len(checks)} follow-up checks")],
    }


async def plan_challenges(state: CaseState, runtime: Runtime[Deps]) -> dict:
    deps = runtime.context
    active = [b for b in state["branches"].values() if b["status"] == "active"]
    plans = await deps.roles.plan_challenges(state, active)
    checks, updates = [], {}
    planned = {p.branch_id: p for p in plans.challenges}
    for b in active:
        nb = dict(b)
        nb["status"] = "active"
        nb["depth"] = 3
        p = planned.get(b["id"])
        if p:
            checks.append({**p.check.model_dump(), "purpose": "challenge", "branch_id": b["id"]})
        else:
            nb["notes"] = b["notes"] + ["Round 3: no challenge was planned."]
        updates[b["id"]] = nb
    return {
        "branches": updates,
        "pending_checks": checks,
        "round": 3,
        "events": [_emit("critic", f"Round 3 - final challenge planned for {', '.join(planned) or 'none'}")],
    }


# ===========================================================================
# 8. Coordinator: case file
# ===========================================================================
def _published_ts(e: dict) -> float:
    try:
        return datetime.fromisoformat(e["published_at"]).timestamp()
    except (KeyError, TypeError, ValueError):
        return float("inf")


async def write_report(state: CaseState, runtime: Runtime[Deps]) -> dict:
    deps, s = runtime.context, runtime.context.settings
    verdict = decide_verdict(state["branches"], s.leading_min_score)
    skipped_challenge = (
        (state.get("round") or 0) == 2
        and (state.get("checks_used") or 0) < s.max_evidence_checks
        and any(b["status"] == "active" for b in state["branches"].values())
    )

    # Final statuses for display.
    updates = {}
    for bid, b in state["branches"].items():
        nb = dict(b)
        if bid in verdict["leading_ids"]:
            nb["status"] = "leading"
        elif b["status"] == "active":
            nb["status"] = "finalist" if b.get("challenge_completed") else "not_established"
            if skipped_challenge:
                nb["notes"] = b["notes"] + [
                    "Challenge round skipped: no explanation had both a documented event and "
                    "pre-close timing, so none could qualify as leading."
                ]
        nb["rank"] = verdict["ranks"].get(bid)
        nb["blockers"] = verdict["blockers"].get(bid, [])
        updates[bid] = nb
    merged = {**state["branches"], **updates}

    try:
        narrative = await deps.roles.write_report({**state, "branches": merged}, verdict)
    except Exception as exc:
        log.exception("report narrative failed")
        narrative = ReportNarrative(
            headline=f"{state['ticker']} {state['anomaly']['ret']:+.2%} move on {state['anomaly']['event_date']}",
            why_it_moved="",
            key_events=[],
            summary=f"The narrative could not be generated ({exc}). Scores and evidence below are complete.",
            explanations=[],
            uncertainties=[],
            next_steps=[],
        )
    narr = {e.branch_id: e.narrative for e in narrative.explanations}
    # Key events: only company news/filings public by the close, ids that exist.
    ev = state["evidence"]
    key_events = sorted(
        (
            {"evidence_id": k.evidence_id, "headline": k.headline, "brief": k.brief}
            for k in {k.evidence_id: k for k in narrative.key_events}.values()  # de-dupe ids
            if k.evidence_id in ev
            and ev[k.evidence_id]["kind"] in ("news", "sec_filing")
            and ev[k.evidence_id]["timing"] in ("before", "during")
            and in_news_window(ev[k.evidence_id], date.fromisoformat(state["anomaly"]["event_date"]), 7)
        ),
        # Parse: published_at strings mix UTC (news) and ET (filings) offsets.
        key=lambda k: _published_ts(ev[k["evidence_id"]]),
    )
    explanations = sorted(
        (
            {
                "branch_id": b["id"],
                "title": b["title"],
                "category": b["category"],
                "statement": b["statement"],
                "status": b["status"],
                "rank": b.get("rank"),
                "score": b.get("score"),
                "narrative": narr.get(b["id"], ""),
                "blockers": b.get("blockers", []),
                "notes": b.get("notes", []),
            }
            for b in merged.values()
        ),
        key=lambda e: (e["rank"] or 99, e["branch_id"]),
    )
    report = {
        "headline": narrative.headline,
        "why_it_moved": narrative.why_it_moved,
        "summary": narrative.summary,
        "rounds_run": state.get("round") or 0,
        "key_events": key_events,
        "verdict": verdict,
        "explanations": explanations,
        "uncertainties": narrative.uncertainties,
        "next_steps": narrative.next_steps,
        "checks_used": state.get("checks_used") or 0,
        "max_checks": s.max_evidence_checks,
        "disclaimer": DISCLAIMER,
        "generated_at": now_iso(),
    }
    label = {"leading": "leading explanation identified", "unresolved": "cause unresolved",
             "needs_review": "leading explanation withheld for analyst review"}[verdict["status"]]
    return {
        "branches": updates,
        "report": report,
        "status": "complete",
        "events": (
            [_emit("controller", "Skipping challenge round - no finalist could qualify as leading")]
            if skipped_challenge else []
        ) + [_emit("coordinator", f"Case file written - {label}")],
    }
