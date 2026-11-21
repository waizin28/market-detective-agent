"""The four agent roles (Week 5): coordinator, hypothesis analyst, evidence
researcher, critic. Each role is a thin layer over a chat model with a typed
structured output; the graph controller decides what runs when.
"""

from __future__ import annotations

import json

from langchain.agents import create_agent
from langchain.agents.middleware import ModelCallLimitMiddleware, ToolCallLimitMiddleware
from langchain.agents.structured_output import ProviderStrategy
from langchain_core.messages import HumanMessage, SystemMessage

from app.agents import prompts
from app.agents.evidence import Ledger, render_evidence
from app.agents.llm import chat_model, structured
from app.agents.tools import build_research_tools
from app.models import (
    ChallengePlans,
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
    """Production roles backed by Claude via LangChain."""

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
        msg = (
            f"## Anomaly\n{anomaly_brief(state)}\n"
            f"## Verdict (fixed by the rules)\n{json.dumps(verdict, indent=1)}\n"
            f"## Explanations and checks\n{branches_brief(branches, state.get('findings'))}\n"
            f"## Evidence gaps\n" + ("\n".join(state.get("gaps", [])[-20:]) or "none") + "\n"
            f"## Evidence\n{render_evidence(state['evidence'], 300)}\n\n"
            "Write the narrative. Include one explanation entry per branch id above."
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
        msg = (
            f"## Anomaly\n{anomaly_brief(state)}\n"
            f"## Explanations and checks so far\n{branches_brief(branches, state.get('findings'))}\n"
            f"## Evidence\n{render_evidence(state['evidence'], 600)}\n\n"
            "Assess every explanation listed above (one assessment per branch id)."
        )
        return await structured("critic", CriticBatch).ainvoke(
            [SystemMessage(prompts.CRITIC), HumanMessage(msg)]
        )

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
