"""Domain models shared by the graph, the agents and the API.

Graph state stores plain dicts (``model_dump()``) so checkpoints stay
JSON-friendly; these models are the schema for those dicts and are also the
structured-output schemas the LLM roles must fill in.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Timing = Literal["before", "during", "after", "unknown"]
SourceKind = Literal["sec_filing", "news", "market_data", "macro_data", "computed"]
HypothesisCategory = Literal[
    "earnings_guidance",
    "company_specific",
    "sector_wide",
    "market_wide",
    "macro",
    "regulatory",
    "analyst_action",
    "corporate_action",
    "other",
]
BranchStatus = Literal["active", "pruned", "ruled_out", "finalist", "leading", "not_established"]


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------
class Evidence(BaseModel):
    """One retrieved, timestamped piece of evidence. Created only by Python tools,
    never by the LLM - agents can cite evidence only by id."""

    id: str
    kind: SourceKind
    title: str
    source: str  # publisher / dataset, e.g. "SEC EDGAR", "Reuters", "FRED"
    url: str | None = None
    published_at: str | None = None  # ISO-8601, timezone aware
    date_only: bool = False  # True when only the calendar date is known
    retrieved_at: str
    timing: Timing = "unknown"
    snippet: str = ""
    # Groups duplicate coverage of one underlying event so it cannot count as
    # independent corroboration (e.g. ten articles about one 8-K).
    story_key: str | None = None
    flags: list[str] = Field(default_factory=list)  # e.g. ["possible_injection"]
    meta: dict = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Anomaly detection
# ---------------------------------------------------------------------------
class PeerMove(BaseModel):
    ticker: str
    role: Literal["market", "sector", "peer"]
    ret: float | None
    available: bool = True


class Anomaly(BaseModel):
    ticker: str
    event_date: str  # trading date of the move (YYYY-MM-DD)
    prev_date: str
    close: float
    prev_close: float
    ret: float  # simple daily return
    mean_ret: float
    std_ret: float
    z_score: float
    volume: float | None = None
    volume_ratio: float | None = None  # volume / 20-day average volume
    lookback_days: int
    flagged: bool
    direction: Literal["up", "down"]
    benchmarks: list[PeerMove] = Field(default_factory=list)
    excess_vs_market: float | None = None
    excess_vs_sector: float | None = None
    beta: float | None = None
    abnormal_ret: float | None = None  # market-model residual return
    abnormal_z: float | None = None
    scope: Literal["company_specific", "sector_wide", "market_wide", "mixed"] = "mixed"
    data_issues: list[str] = Field(default_factory=list)


class PricePoint(BaseModel):
    date: str
    close: float
    volume: float | None = None


# ---------------------------------------------------------------------------
# Hypothesis tree (structured outputs for the analyst / critic roles)
# ---------------------------------------------------------------------------
class EvidenceCheck(BaseModel):
    """A single evidence check the researcher should perform."""

    question: str = Field(description="The specific question the check should answer.")
    suggested_sources: list[Literal["sec_filings", "news", "market_data", "macro_data"]] = Field(
        description="Which tool families are most likely to answer it."
    )
    purpose: Literal["initial", "refine", "challenge"] = "initial"


class ProposedHypothesis(BaseModel):
    title: str = Field(description="Short label, e.g. 'Weaker next-quarter guidance'.")
    category: HypothesisCategory
    statement: str = Field(description="The explanation, stated as a testable claim.")
    prediction: str = Field(description="What evidence should exist if this is true.")
    would_weaken: str = Field(description="What evidence would weaken or disprove it.")
    initial_check: EvidenceCheck


class HypothesisProposal(BaseModel):
    hypotheses: list[ProposedHypothesis] = Field(
        description="Two to four distinct, competing explanations."
    )


class Refinement(BaseModel):
    branch_id: str
    refined_statement: str = Field(description="Sharper version of the hypothesis.")
    checks: list[EvidenceCheck] = Field(description="Up to two follow-up checks.")


class RefinementPlan(BaseModel):
    refinements: list[Refinement]


class ChallengePlan(BaseModel):
    branch_id: str
    check: EvidenceCheck = Field(description="One check designed to DISPROVE the hypothesis.")


class ChallengePlans(BaseModel):
    challenges: list[ChallengePlan]


class Finding(BaseModel):
    """Researcher output for one evidence check."""

    summary: str = Field(description="What the evidence shows, citing evidence ids like [E3].")
    outcome: Literal["supports", "contradicts", "mixed", "insufficient"]
    supporting_evidence_ids: list[str] = Field(default_factory=list)
    contradicting_evidence_ids: list[str] = Field(default_factory=list)


class CriticAssessment(BaseModel):
    """Critic's judgement on one branch. Python re-checks timing and independence."""

    branch_id: str
    event_documented: bool = Field(description="A source directly documents the event.")
    event_evidence_ids: list[str] = Field(
        default_factory=list, description="Evidence ids that document the event itself."
    )
    price_fit: bool = Field(description="Stock/peer/benchmark returns fit the explanation.")
    independent_support: bool = Field(
        description="A separate source or dataset supports it (repeat coverage counts once)."
    )
    supporting_evidence_ids: list[str] = Field(default_factory=list)
    contradicting_evidence_ids: list[str] = Field(default_factory=list)
    decisive_contradiction: bool = Field(
        description="True only if evidence clearly rules the explanation out."
    )
    sources_conflict: bool = Field(
        default=False,
        description="Credible sources materially disagree about a fact this explanation needs.",
    )
    rationale: str


class CriticBatch(BaseModel):
    assessments: list[CriticAssessment]


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------
class ExplanationNarrative(BaseModel):
    branch_id: str
    narrative: str = Field(description="2-4 sentences, citing evidence ids like [E3].")


class KeyEvent(BaseModel):
    evidence_id: str = Field(description="Id of a news (preferred) or SEC-filing evidence item about this company.")
    headline: str = Field(
        description=(
            "Crisp headline, max ~12 words: WHO did WHAT, in plain English, with the key "
            "number if any. E.g. 'Reuters: SEC probing Nvidia's accounting' or 'CEO Jane "
            "Doe resigns after board review'. Never a form name like '8-K Item 5.02'."
        )
    )
    brief: str = Field(
        description=(
            "1-2 plain sentences: what happened, and how it relates to the stock's move "
            "(e.g. 'raised concerns about X, which the sell-off followed'). No citations needed."
        )
    )


class ReportNarrative(BaseModel):
    headline: str = Field(description="One line, neutral, no investment advice.")
    why_it_moved: str = Field(
        description=(
            "3-5 sentences answering 'why did the stock go up/down?' for the reader. State the "
            "direction and size, put it in the context of the preceding weeks (trend and the "
            "developments that built up to it), how much of the move was shared with the sector/market vs. "
            "company-specific, and the most likely driver(s) the evidence points to - clearly "
            "labeled as 'established' (leading explanation) or 'most likely but not confirmed' "
            "(unresolved). Cite evidence ids."
        )
    )
    summary: str = Field(
        description="One paragraph summary over the wide timeline (the weeks leading up, then the event day) that cites evidence ids."
    )
    key_events: list[KeyEvent] = Field(
        description=(
            "4-10 events, mostly news articles, about THIS company across the whole lead-up (the weeks before, not "
            "just the event day), public before the event-day close, that together tell the "
            "story of how the move built up. Skip generic market stories, price-quote pages, "
            "duplicates and anything published after the close."
        )
    )
    explanations: list[ExplanationNarrative]
    uncertainties: list[str]
    next_steps: list[str] = Field(description="Things a researcher could monitor or check next.")


class PeerPlan(BaseModel):
    peers: list[str] = Field(description="Two or three US-listed peer tickers.")
    rationale: str


class FollowUpAnswer(BaseModel):
    answer: str = Field(description="Answer grounded in the case file, citing evidence ids.")
    cited_evidence_ids: list[str] = Field(default_factory=list)
    outside_case_scope: bool = False
