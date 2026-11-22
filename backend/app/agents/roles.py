"""The four agent roles (Week 5): coordinator, hypothesis analyst, evidence
researcher, critic. Each role is a thin layer over a chat model with a typed
structured output; the graph controller decides what runs when.
"""

from __future__ import annotations

import asyncio
import json

from langchain.agents import create_agent
from langchain.agents.middleware import ModelCallLimitMiddleware, ToolCallLimitMiddleware
from langchain.agents.structured_output import ProviderStrategy
from langchain_core.messages import HumanMessage, SystemMessage

from app.agents import prompts
from datetime import date, timedelta

from app.agents.evidence import Ledger, in_news_window, render_evidence
from app.agents.llm import chat_model, structured
from app.agents.tools import build_research_tools
from app.models import (
    ChallengePlans,
    CriticAssessment,
    CriticBatch,
    Finding,
    FollowUpAnswer,
    HypothesisProposal,
    PeerPlan,
    RefinementPlan,
    ReportNarrative,
)


def anomaly_brief(state: dict) -> str:
    a = state.get("anomaly") or {}
    if not a:
        return "(no anomaly computed)"
    bm = ", ".join(
        f"{b['ticker']} ({b['role']}) {b['ret']:+.2%}" if b.get("ret") is not None else f"{b['ticker']} n/a"
        for b in a.get("benchmarks", [])
    )

    def pct(x):
        return "n/a" if x is None else f"{x:+.2%}"

    lines = [
        f"Company: {state.get('company')} ({a['ticker']})",
        f"Move: {pct(a['ret'])} on {a['event_date']} (close {a['prev_close']:.2f} -> {a['close']:.2f}; "
        f"window = {a['prev_date']} 4:00 PM ET close -> {a['event_date']} 4:00 PM ET close)",
        f"z-score vs prior {a['lookback_days']} trading days: {a['z_score']:+.2f} "
        f"(mean {a['mean_ret']:+.2%}, sd {a['std_ret']:.2%}); flagged={a['flagged']}",
    ]
    if a.get("volume_ratio"):
        lines.append(f"Volume vs 20-day average: {a['volume_ratio']:.1f}x")
    lines.append(f"Benchmarks/peers same day: {bm or 'none'}")
    lines.append(
        f"Excess vs market: {pct(a.get('excess_vs_market'))}; excess vs sector: {pct(a.get('excess_vs_sector'))}"
    )
    if a.get("beta") is not None:
        lines.append(f"Beta {a['beta']:.2f}; market-model abnormal return {pct(a.get('abnormal_ret'))}")
    lines.append(f"Scope classification (deterministic): {a.get('scope')}")
    if a.get("data_issues"):
        lines.append("Data issues: " + "; ".join(a["data_issues"]))
    return "\n".join(lines) + "\n"


def trend_brief(state: dict) -> str:
    """Wide-timeline price context: how the stock and benchmarks moved over the weeks
    before the event, so the narrative can describe the build-up, not just one day."""
    a = state.get("anomaly") or {}
    ev_date = a.get("event_date")

    def window(points: list[dict]) -> str | None:
        pts = [p for p in points or [] if p["date"] <= ev_date]
        if len(pts) < 22:
            return None
        last = pts[-1]["close"]
        r5 = last / pts[-6]["close"] - 1
        r21 = last / pts[-22]["close"] - 1
        peak = max(p["close"] for p in pts[-63:])
        return f"5-day {r5:+.1%}, ~1-month {r21:+.1%}, {last / peak - 1:+.1%} from its 3-month high"

    lines = []
    own = window(state.get("prices") or [])
    if own:
        lines.append(f"{state.get('ticker')} through {ev_date}: {own}")
    for t, pts in (state.get("benchmark_prices") or {}).items():
        w = window(pts)
        if w:
            lines.append(f"{t}: {w}")
    return "\n".join(lines) or "(not available)"


def focused_evidence(state: dict, branch_id: str) -> dict[str, dict]:
    """Evidence a critic needs for one branch: market/computed/macro data, company news and
    filings public by the close, and anything this branch's checks found or cited."""
    own: set[str] = set()
    for f in state.get("findings") or []:
        if f["branch_id"] == branch_id:
            own.update(f.get("new_evidence_ids") or [])
            own.update(f.get("supporting_evidence_ids") or [])
            own.update(f.get("contradicting_evidence_ids") or [])
    return {
        k: v
        for k, v in state["evidence"].items()
        if k in own
        or v["kind"] in ("computed", "market_data", "macro_data")
        or (v["kind"] in ("news", "sec_filing") and v["timing"] in ("before", "during"))
    }


def branches_brief(branches: list[dict], findings: list[dict] | None = None) -> str:
    out = []
    for b in branches:
        s = b.get("score") or {}
        out.append(
            f"[{b['id']}] {b['title']} (category: {b['category']}, status: {b['status']})\n"
            f"  Statement: {b['statement']}\n  Predicts: {b['prediction']}\n"
            f"  Would weaken it: {b['would_weaken']}"
            + (f"\n  Current score: {s.get('total')}/4" if s else "")
        )
        for f in findings or []:
            if f["branch_id"] == b["id"]:
                out.append(
                    f"  - Check ({f['purpose']}, round {f['round']}): {f['question']}\n"
                    f"    Status: {f['status']}. Outcome: {f.get('outcome')}. {f.get('summary', '')}"
                )
    return "\n".join(out)


class LLMRoles:
    """Production roles backed by OpenAI (ChatGPT models) via LangChain."""

    def __init__(self, researcher_max_tool_calls: int = 4):
        self._max_tools = researcher_max_tool_calls

    # ---------------------------------------------------------------- coordinator
    async def propose_peers(self, company: str, ticker: str, sector: str | None, industry: str | None) -> PeerPlan:
        msg = f"Company: {company} ({ticker}). Sector: {sector or 'unknown'}. Industry: {industry or 'unknown'}."
        return await structured("coordinator", PeerPlan).ainvoke(
            [SystemMessage(prompts.COORDINATOR_PEERS), HumanMessage(msg)]
        )

    async def write_report(self, state: dict, verdict: dict) -> ReportNarrative:
        branches = sorted(state["branches"].values(), key=lambda b: verdict["ranks"].get(b["id"], 99))
        # The summary considers news from a week before the analyzed day through that day.
        ev_day = date.fromisoformat(state["anomaly"]["event_date"])
        evidence = {k: v for k, v in state["evidence"].items() if in_news_window(v, ev_day, 7)}
        msg = (
            f"## Anomaly\n{anomaly_brief(state)}\n"
            f"## Wide timeline: price trend into the event (computed)\n{trend_brief(state)}\n"
            f"## Verdict (fixed by the rules)\n{json.dumps(verdict, indent=1)}\n"
            f"## Explanations and checks\n{branches_brief(branches, state.get('findings'))}\n"
            f"## Evidence gaps\n" + ("\n".join(state.get("gaps", [])[-20:]) or "none") + "\n"
            f"## Evidence (news limited to {ev_day - timedelta(days=7)} through {ev_day})\n"
            f"{render_evidence(evidence, 300)}\n\n"
            "Write the narrative. Include one explanation entry per branch id above."
        )
        return await structured("coordinator", ReportNarrative).ainvoke(
            [SystemMessage(prompts.COORDINATOR_REPORT), HumanMessage(msg)]
        )

    async def brief_normal_day(self, state: dict) -> ReportNarrative:
        """One call for a normal-range move: what the week's news says, no hypothesis search."""
        ev_day = date.fromisoformat(state["anomaly"]["event_date"])
        evidence = {k: v for k, v in state["evidence"].items() if in_news_window(v, ev_day, 7)}
        msg = (
            f"## Anomaly\n{anomaly_brief(state)}\n"
            f"## Wide timeline: price trend into the day (computed)\n{trend_brief(state)}\n"
            "## Verdict (fixed by the rules)\nThe move was within the stock's normal range, so no "
            "competing explanations were tested. Explain the day and the week's news; don't claim a "
            "cause was established. Return an empty explanations list.\n"
            f"## Evidence (news limited to {ev_day - timedelta(days=7)} through {ev_day})\n"
            f"{render_evidence(evidence, 300)}\n"
        )
        return await structured("coordinator", ReportNarrative).ainvoke(
            [SystemMessage(prompts.COORDINATOR_REPORT), HumanMessage(msg)]
        )

    async def answer(self, state: dict, question: str, history: list[dict]) -> FollowUpAnswer:
        report = state.get("report") or {}
        prior = "\n".join(f"{m['role']}: {m['content']}" for m in history[-6:])
        msg = (
            f"## Case file\n{json.dumps({k: report.get(k) for k in ('headline', 'summary', 'verdict', 'explanations', 'uncertainties')}, indent=1, default=str)}\n"
            f"## Anomaly\n{anomaly_brief(state)}\n"
            f"## Evidence\n{render_evidence(state['evidence'], 500)}\n"
            f"## Conversation so far\n{prior or '(none)'}\n\n## Question\n{question}"
        )
        return await structured("coordinator", FollowUpAnswer).ainvoke(
            [SystemMessage(prompts.FOLLOW_UP), HumanMessage(msg)]
        )

    # ---------------------------------------------------------------- analyst
    async def propose_hypotheses(self, state: dict, max_n: int) -> HypothesisProposal:
        msg = (
            f"## Anomaly\n{anomaly_brief(state)}\n"
            f"## Initial timeline\n{render_evidence(state['evidence'], 300)}\n\n"
            f"Propose between 2 and {max_n} distinct competing explanations, each with one initial evidence check."
        )
        return await structured("analyst", HypothesisProposal).ainvoke(
            [SystemMessage(prompts.ANALYST), HumanMessage(msg)]
        )

    async def refine(self, state: dict, branches: list[dict], max_checks: int) -> RefinementPlan:
        msg = (
            f"## Anomaly\n{anomaly_brief(state)}\n"
            f"## Surviving explanations\n{branches_brief(branches, state.get('findings'))}\n"
            f"## Evidence\n{render_evidence(state['evidence'], 250)}\n\n"
            f"For each surviving explanation, sharpen the statement (e.g. distinguish a lower sales "
            f"forecast from narrower margins) and propose up to {max_checks} follow-up checks that would "
            f"discriminate between the refined versions. Use the branch ids shown."
        )
        return await structured("analyst", RefinementPlan).ainvoke(
            [SystemMessage(prompts.ANALYST), HumanMessage(msg)]
        )

    # ---------------------------------------------------------------- critic
    async def plan_challenges(self, state: dict, branches: list[dict]) -> ChallengePlans:
        msg = (
            f"## Anomaly\n{anomaly_brief(state)}\n"
            f"## Finalists\n{branches_brief(branches, state.get('findings'))}\n"
            f"## Evidence\n{render_evidence(state['evidence'], 250)}\n\n"
            "Design exactly one disconfirming check per finalist. Use the branch ids shown."
        )
        return await structured("critic", ChallengePlans).ainvoke(
            [SystemMessage(prompts.CHALLENGER), HumanMessage(msg)]
        )

    async def critique(self, state: dict, branches: list[dict]) -> CriticBatch:
        """One critic call per branch, run in parallel, each seeing only the evidence
        relevant to that branch. Faster (wall time = slowest single call) and fewer
        input tokens than one big call over the whole ledger."""

        async def one(b: dict) -> CriticAssessment | None:
            evidence = focused_evidence(state, b["id"])
            msg = (
                f"## Anomaly\n{anomaly_brief(state)}\n"
                f"## Explanation and its checks so far\n{branches_brief([b], state.get('findings'))}\n"
                f"## Evidence\n{render_evidence(evidence, 400)}\n\n"
                f"Assess explanation [{b['id']}] (use branch_id \"{b['id']}\")."
            )
            try:
                a = await structured("critic", CriticAssessment).ainvoke(
                    [SystemMessage(prompts.CRITIC), HumanMessage(msg)]
                )
            except Exception:  # the node records a missing assessment for this branch
                return None
            a.branch_id = b["id"]
            return a

        results = await asyncio.gather(*(one(b) for b in branches))
        return CriticBatch(assessments=[a for a in results if a is not None])

    # ---------------------------------------------------------------- researcher
    async def research(self, state: dict, branch: dict, check: dict, ledger: Ledger) -> Finding:
        agent = create_agent(
            model=chat_model("researcher"),
            tools=build_research_tools(ledger),
            system_prompt=prompts.RESEARCHER,
            response_format=ProviderStrategy(Finding),
            middleware=[
                # Runtime limits: tool calls past the cap are refused, and the
                # model-call cap bounds the whole ReAct loop.
                ToolCallLimitMiddleware(run_limit=self._max_tools, exit_behavior="continue"),
                ModelCallLimitMiddleware(run_limit=self._max_tools + 3, exit_behavior="end"),
            ],
            name=f"researcher_{branch['id']}",
        )
        known = {k: v for k, v in state["evidence"].items() if v["kind"] in ("sec_filing", "market_data")}
        msg = (
            f"## Anomaly\n{anomaly_brief(state)}\n"
            f"## Hypothesis [{branch['id']}] {branch['title']}\n{branch['statement']}\n"
            f"Predicts: {branch['prediction']}\nWould weaken it: {branch['would_weaken']}\n"
            f"## Evidence check ({check['purpose']})\n{check['question']}\n"
            f"Suggested sources: {', '.join(check.get('suggested_sources', []))}\n"
            f"## Already-known filings and market data\n{render_evidence(known, 150)}\n"
        )
        result = await agent.ainvoke({"messages": [HumanMessage(msg)]})
        finding = result.get("structured_response")
        if finding is None:
            return Finding(
                summary="The researcher hit its tool/model-call limit before reporting a finding.",
                outcome="insufficient",
            )
        return finding
