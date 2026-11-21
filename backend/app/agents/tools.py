"""Read-only LangChain tools for the evidence researcher.

Tools are built per evidence check around a ``Ledger`` so every result is
recorded as citable evidence. Failures return an UNAVAILABLE message: missing
data is an evidence gap, never evidence against a hypothesis.
"""

from __future__ import annotations

from datetime import date

from langchain_core.tools import BaseTool, tool

from app.agents.evidence import Ledger, render_evidence
from app.data.http import BudgetExceeded, DataUnavailable


def _unavailable(ledger: Ledger, what: str, exc: Exception) -> str:
    msg = f"{what}: {exc}"
    ledger.gaps.append(msg)
    return f"UNAVAILABLE - {msg}. Record this as missing evidence, not as evidence against."


def build_research_tools(ledger: Ledger, top_k: int = 5) -> list[BaseTool]:
    ctx = ledger.ctx

    @tool
    async def search_news(query: str, days_before: int = 7, days_after: int = 1) -> str:
        """Search financial news around the event date. Use specific queries
        (company name plus a topic such as 'guidance', 'export restrictions',
        'downgrade'). Articles published after the close are marked timing="after"."""
        try:
            items = await ledger.news(query, max(0, min(days_before, 30)), max(0, min(days_after, 5)))
        except (DataUnavailable, BudgetExceeded) as exc:
            return _unavailable(ledger, "news search", exc)
        return render_evidence(items) if items else "No articles found for that query and window."

    @tool
    async def list_sec_filings(days_before: int = 30, days_after: int = 2) -> str:
        """List the company's SEC filings (8-K, 10-Q, 10-K, ...) around the event,
        with EDGAR acceptance timestamps. Returns filing evidence ids that can be
        passed to search_sec_filing."""
        try:
            items = await ledger.filings(max(0, min(days_before, 120)), max(0, min(days_after, 10)))
        except (DataUnavailable, BudgetExceeded) as exc:
            return _unavailable(ledger, "SEC filing list", exc)
        return render_evidence(items) if items else "No relevant SEC filings in that window."

    @tool
    async def search_sec_filing(filing_evidence_id: str, question: str) -> str:
        """Semantic search inside one SEC filing (and its press-release exhibit).
        Returns the most relevant passages, e.g. question='next quarter revenue
        outlook' or 'gross margin guidance'."""
        try:
            items = await ledger.search_filing(filing_evidence_id, question, top_k)
        except (DataUnavailable, BudgetExceeded) as exc:
            return _unavailable(ledger, f"filing search {filing_evidence_id}", exc)
        return render_evidence(items, snippet_chars=1500)

    @tool
    async def get_daily_returns(tickers: list[str], on_date: str | None = None) -> str:
        """Daily percentage returns for up to 6 tickers (peers, ETFs, indices) on
        the event date, or on another YYYY-MM-DD date. Use this to test whether
        peers or the sector moved the same way."""
        try:
            on = date.fromisoformat(on_date) if on_date else None
        except ValueError:
            return "Invalid date; use YYYY-MM-DD."
        items = await ledger.returns(tickers, on)
        gaps = [g for g in ledger.gaps if "Return for" in g]
        text = render_evidence(items) if items else "No returns available."
        return text + ("\nGaps: " + "; ".join(gaps[-6:]) if gaps else "")

    @tool
    async def get_macro_context(series: list[str] | None = None) -> str:
        """Treasury yield / VIX moves on the event date (FRED series such as
        DGS10, DGS2, VIXCLS, DFF) and major scheduled macro releases (CPI, jobs,
        FOMC) in the three days up to the event."""
        try:
            items = await ledger.macro(series)
        except (DataUnavailable, BudgetExceeded) as exc:
            return _unavailable(ledger, "macro data", exc)
        return render_evidence(items) if items else "No macro data for that date."

    return [search_news, list_sec_filings, search_sec_filing, get_daily_returns, get_macro_context]
