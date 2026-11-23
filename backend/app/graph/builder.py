"""Assemble the Market Detective StateGraph."""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph
from langgraph.types import RetryPolicy

from app.graph import nodes
from app.graph.deps import Deps
from app.graph.state import CaseState

# LLM/network hiccups get a couple of bounded retries per node.
LLM_RETRY = RetryPolicy(max_attempts=2, initial_interval=2.0)


def build_graph(checkpointer=None):
    g = StateGraph(CaseState, context_schema=Deps)

    g.add_node("resolve_case", nodes.resolve_case)
    g.add_node("detect_anomaly", nodes.detect_anomaly)
    g.add_node("no_anomaly_report", nodes.no_anomaly_report)
    g.add_node("build_timeline", nodes.build_timeline)
    g.add_node("propose_hypotheses", nodes.propose_hypotheses, retry_policy=LLM_RETRY)
    g.add_node("research_check", nodes.research_check)
    g.add_node("critique", nodes.critique, retry_policy=LLM_RETRY)
    g.add_node("controller", nodes.controller)
    g.add_node("refine_branches", nodes.refine_branches, retry_policy=LLM_RETRY)
    g.add_node("plan_challenges", nodes.plan_challenges, retry_policy=LLM_RETRY)
    g.add_node("write_report", nodes.write_report)

    g.add_edge(START, "resolve_case")
    g.add_edge("resolve_case", "detect_anomaly")
    # Every case gets the timeline (news, filings, peers - no LLM). Normal-range
    # moves then get a one-call news brief instead of the full hypothesis search.
    g.add_edge("detect_anomaly", "build_timeline")
    g.add_conditional_edges(
        "build_timeline", nodes.route_after_timeline, ["propose_hypotheses", "no_anomaly_report"]
    )
    g.add_edge("no_anomaly_report", END)

    # Each planning step fans out its evidence checks in parallel.
    for planner in ("propose_hypotheses", "refine_branches", "plan_challenges"):
        g.add_conditional_edges(planner, nodes.dispatch_checks, ["research_check", "write_report"])

    g.add_edge("research_check", "critique")
    g.add_edge("critique", "controller")
    g.add_conditional_edges(
        "controller",
        nodes.route_after_controller,
        ["refine_branches", "plan_challenges", "write_report"],
    )
    g.add_edge("write_report", END)
    return g.compile(checkpointer=checkpointer, name="market_detective")
