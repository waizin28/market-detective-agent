"""Evidence ledger and the read-only research operations shared by the timeline
builder and the researcher agent's tools.

Evidence records are created only here, by Python, with timestamps and timing
classification computed deterministically. The LLM can cite evidence only by
the ids this module returns.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from app.analysis.anomaly import daily_returns
from app.analysis.timing import classify
from app.analysis import intraday as intra
from app.data import market as mkt
from app.data.http import BudgetExceeded, DataUnavailable
from app.data.sec import ET, Filing
from app.models import Evidence

INJECTION_PATTERNS = re.compile(
    r"(ignore|disregard|forget)\s+(all\s+|any\s+)?(previous|prior|above|earlier)\s+(instructions|prompts?|rules)"
    r"|you\s+are\s+now\s+|new\s+instructions\s*:|system\s+prompt|act\s+as\s+(an?\s+)?(admin|developer)"
    r"|call\s+the\s+\w+\s+tool|<\s*/?\s*(system|assistant|instructions?)\s*>",
    re.I,
)


def evidence_id(*parts: str) -> str:
    h = hashlib.sha1("|".join(parts).encode()).hexdigest()[:6].upper()
    return f"E-{h}"


def scan_injection(text: str) -> list[str]:
    return ["possible_injection"] if INJECTION_PATTERNS.search(text or "") else []


def price_history_url(ticker: str) -> str:
    """Public page where daily closes can be checked by hand."""
    return f"https://finance.yahoo.com/quote/{ticker}/history/"


def in_news_window(e: dict, event_date: date, days: int = 7) -> bool:
    """Non-news items always pass. News passes when published (in ET) from ``days``
    before ``event_date`` through the end of ``event_date``."""
    if e.get("kind") != "news":
        return True
    try:
        dt = datetime.fromisoformat(e["published_at"])
        d = dt.astimezone(ET).date() if dt.tzinfo else dt.date()
    except (KeyError, TypeError, ValueError):
        return False
    return event_date - timedelta(days=days) <= d <= event_date


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _first_word(name: str) -> str:
    w = re.split(r"[\s,.]+", name.strip())[0].lower() if name else ""
    return w if len(w) >= 3 else ""


@dataclass
class CaseContext:
    """What the research operations need to know about the case."""

    ticker: str
    company: str
    cik: int | None
    event_date: date
    prev_date: date
    deps: object  # app.graph.deps.Deps (kept untyped to avoid an import cycle)
    filings: dict[str, Filing] = field(default_factory=dict)  # evidence id -> filing


class Ledger:
    """Collects evidence produced during one node execution."""

    def __init__(self, ctx: CaseContext, existing: dict[str, dict] | None = None):
        self.ctx = ctx
        self.items: dict[str, dict] = {}
        self._existing = existing or {}
        self.gaps: list[str] = []
        # Re-hydrate filings found earlier in the case so search_sec_filing can
        # open them without the researcher re-listing filings.
        for eid, e in self._existing.items():
            m = e.get("meta") or {}
            if e.get("kind") == "sec_filing" and m.get("primary_document") and eid not in ctx.filings:
                ctx.filings[eid] = Filing(
                    form=m["form"], accession=m["accession"], filing_date=m["filing_date"],
                    accepted_at=datetime.fromisoformat(m["accepted_at"]) if m.get("accepted_at") else None,
                    primary_document=m["primary_document"], items=m.get("items", []), cik=m["cik"],
                )

    def add(self, ev: Evidence) -> dict:
        d = ev.model_dump()
        self.items[ev.id] = d
        return d

    def timing(self, published: datetime | None, date_only: bool = False) -> str:
        return classify(
            published, event_date=self.ctx.event_date, prev_date=self.ctx.prev_date, date_only=date_only
        )

    # ---------------------------------------------------------------- news
    async def news(self, query: str, days_before: int = 7, days_after: int = 1, limit: int = 12) -> list[dict]:
        news = self.ctx.deps.news
        start = self.ctx.event_date - timedelta(days=days_before)
        end = self.ctx.event_date + timedelta(days=days_after)
        arts = await news.search(query, start, end, ticker=self.ctx.ticker, limit=limit)
        # Keep only articles that mention the company: headline feeds mix in
        # unrelated market stories that would pollute the timeline.
        names = {self.ctx.ticker.lower(), _first_word(self.ctx.company)}
        arts = [a for a in arts if any(n and n in f"{a.title} {a.description}".lower() for n in names)]
        out = []
        for a in arts:
            snippet = (a.description or "")[:600]
            eid = evidence_id("news", a.url or "", a.title)
            out.append(
                self.add(
                    Evidence(
                        id=eid,
                        kind="news",
                        title=a.title[:300],
                        source=a.source,
                        url=a.url,
                        published_at=a.published_at.isoformat() if a.published_at else None,
                        retrieved_at=now_iso(),
                        timing=self.timing(a.published_at),
                        snippet=snippet,
                        flags=scan_injection(a.title + " " + snippet),
                        meta={"provider": news.provider, **({"publisher_site": a.site} if a.site else {})},
                    )
                )
            )
        return out

    # ---------------------------------------------------------------- SEC
    async def filings(self, days_before: int = 30, days_after: int = 2) -> list[dict]:
        if self.ctx.cik is None:
            raise DataUnavailable("No SEC CIK for this security (ETFs and foreign issuers may lack one)")
        start = self.ctx.event_date - timedelta(days=days_before)
        end = self.ctx.event_date + timedelta(days=days_after)
        out = []
        for f in await self.ctx.deps.sec.filings(self.ctx.cik, start, end):
            eid = evidence_id("sec", f.accession)
            self.ctx.filings[eid] = f
            out.append(
                self.add(
                    Evidence(
                        id=eid,
                        kind="sec_filing",
                        title=f"{self.ctx.ticker} {f.description}",
                        source="SEC EDGAR",
                        url=f.url,
                        published_at=f.accepted_at.isoformat() if f.accepted_at else f.filing_date,
                        date_only=f.accepted_at is None,
                        retrieved_at=now_iso(),
                        timing=self.timing(
                            f.accepted_at
                            or datetime.combine(date.fromisoformat(f.filing_date), datetime.min.time(), tzinfo=ET),
                            date_only=f.accepted_at is None,
                        ),
                        snippet=f"Accepted {f.accepted_at:%Y-%m-%d %H:%M} ET" if f.accepted_at else f"Filed {f.filing_date}",
                        story_key=f.accession,
                        meta={"form": f.form, "items": f.items, "accession": f.accession, "cik": f.cik,
                              "filing_date": f.filing_date, "primary_document": f.primary_document,
                              "accepted_at": f.accepted_at.isoformat() if f.accepted_at else None},
                    )
                )
            )
        return out

    async def search_filing(self, filing_evidence_id: str, query: str, k: int) -> list[dict]:
        f = self.ctx.filings.get(filing_evidence_id)
        if f is None:
            raise DataUnavailable(
                f"{filing_evidence_id} is not a known SEC filing id; call list_sec_filings first"
            )
        text, url = await self.ctx.deps.sec.filing_text(f)
        passages = await self.ctx.deps.retriever.search(f.accession, text, query, k=k)
        published = f.accepted_at
        out = []
        for p in passages:
            eid = evidence_id("sec-passage", f.accession, str(p.chunk_index))
            out.append(
                self.add(
                    Evidence(
                        id=eid,
                        kind="sec_filing",
                        title=f"{self.ctx.ticker} Form {f.form} - {p.section}",
                        source="SEC EDGAR",
                        url=url,
                        published_at=published.isoformat() if published else f.filing_date,
                        date_only=published is None,
                        retrieved_at=now_iso(),
                        timing=self.timing(
                            published
                            or datetime.combine(date.fromisoformat(f.filing_date), datetime.min.time(), tzinfo=ET),
                            date_only=published is None,
                        ),
                        snippet=p.text[:1500],
                        story_key=f.accession,
                        flags=scan_injection(p.text),
                        meta={"form": f.form, "section": p.section, "similarity": round(p.score, 3),
                              "parent": filing_evidence_id},
                    )
                )
            )
        return out

    # ---------------------------------------------------------------- market
    async def returns(self, tickers: list[str], on: date | None = None) -> list[dict]:
        on = on or self.ctx.event_date
        md = self.ctx.deps.market
        out = []
        for t in [t.upper().strip() for t in tickers][:6]:
            try:
                df = await md.history(t, on - timedelta(days=12), on)
                r = daily_returns(df)
                if r.empty or r.index[-1].date() != on:
                    raise DataUnavailable(f"no {t} bar on {on}")
                ret = float(r.iloc[-1])
            except (DataUnavailable, BudgetExceeded) as exc:
                self.gaps.append(f"Return for {t} on {on}: unavailable ({exc})")
                continue
            close_dt = datetime.combine(on, datetime.min.time(), tzinfo=ET).replace(hour=16)
            out.append(
                self.add(
                    Evidence(
                        id=evidence_id("ret", t, on.isoformat()),
                        kind="market_data",
                        title=f"{t} daily return on {on}: {ret:+.2%}",
                        source=df.attrs.get("source", "market data"),
                        url=price_history_url(t),
                        published_at=close_dt.isoformat(),
                        retrieved_at=now_iso(),
                        timing=self.timing(close_dt),
                        snippet=f"{t} closed {float(df['close'].iloc[-1]):.2f} ({ret:+.2%} vs prior close).",
                        meta={"ticker": t, "ret": ret},
                    )
                )
            )
        return out

    # ---------------------------------------------------------------- earnings / analysts
    async def earnings(self, lookback_days: int = 100) -> list[dict]:
        """The latest earnings report at or before the event (EPS vs estimate)."""
        t = self.ctx.ticker
        df = await mkt.earnings_dates(t)
        close = market_close_dt(self.ctx.event_date)
        past = df[(df.index <= close) & df["Reported EPS"].notna()]
        past = past[past.index.date >= self.ctx.event_date - timedelta(days=lookback_days)]
        out = []
        for ts, row in past.tail(1).iterrows():
            est, rep, sur = row.get("EPS Estimate"), row.get("Reported EPS"), row.get("Surprise(%)")
            if not (_num(est) and _num(rep)):
                continue
            sur = float(sur) if _num(sur) else None
            beat = "beat" if rep > est else "missed" if rep < est else "matched"
            when = ts.to_pydatetime()
            sur_txt = f" ({sur:+.1f}% surprise)" if sur is not None else ""
            out.append(
                self.add(
                    Evidence(
                        id=evidence_id("earnings", t, when.isoformat()),
                        kind="earnings",
                        title=f"{t} reported EPS ${rep:.2f} vs ${est:.2f} expected: {beat} estimates{sur_txt}",
                        source="Yahoo Finance earnings calendar",
                        url=f"https://finance.yahoo.com/calendar/earnings?symbol={t}",
                        published_at=when.isoformat(),
                        retrieved_at=now_iso(),
                        timing=self.timing(when),
                        snippet=(
                            f"Earnings released {when:%a %b %d, %Y %-I:%M %p} ET (scheduled time per Yahoo). "
                            f"Reported EPS {rep:.2f}, consensus estimate {est:.2f}{sur_txt}. "
                            f"{(self.ctx.event_date - when.date()).days} days before the move."
                        ),
                        meta={"eps": float(rep), "estimate": float(est), "surprise_pct": sur},
                    )
                )
            )
        return out

    async def analyst_actions(self, days_before: int = 14, limit: int = 10) -> list[dict]:
        """Upgrades, downgrades and price-target changes around the event."""
        t = self.ctx.ticker
        df = await mkt.analyst_actions(t)
        idx = df.index.tz_localize("UTC") if df.index.tz is None else df.index
        df = df.set_axis(idx)
        start = datetime.combine(self.ctx.event_date - timedelta(days=days_before), datetime.min.time(), tzinfo=ET)
        end = market_close_dt(self.ctx.event_date) + timedelta(days=1)
        df = df[(df.index >= start) & (df.index <= end)].tail(limit)
        verbs = {"up": "upgrades", "down": "downgrades", "init": "starts coverage of", "main": "maintains", "reit": "reiterates"}
        out = []
        for ts, r in df.iterrows():
            action = verbs.get(str(r.get("Action", "")).lower(), str(r.get("Action", "")))
            to, frm = r.get("ToGrade") or "", r.get("FromGrade") or ""
            grade = f" to {to}" + (f" from {frm}" if frm and frm != to and action in ("upgrades", "downgrades") else "")
            if action not in ("upgrades", "downgrades"):
                grade = f" at {to}" if to else ""
            cur, prior = r.get("currentPriceTarget") or 0, r.get("priorPriceTarget") or 0
            pt = ""
            if cur:
                pt = f"; target ${prior:,.0f} → ${cur:,.0f}" if prior and prior != cur else f"; target ${cur:,.0f}"
            title = f"{r.get('Firm', 'Analyst')} {action} {t}{grade}{pt}"
            when = ts.to_pydatetime()
            out.append(
                self.add(
                    Evidence(
                        id=evidence_id("analyst", t, str(r.get("Firm")), when.isoformat()),
                        kind="analyst",
                        title=title[:300],
                        source=f"{r.get('Firm', 'Analyst')} (via Yahoo Finance)",
                        url=f"https://finance.yahoo.com/quote/{t}/analysis/",
                        published_at=when.isoformat(),
                        retrieved_at=now_iso(),
                        timing=self.timing(when),
                        snippet=f"{title}. Price-target action: {r.get('priceTargetAction') or 'n/a'}.",
                        meta={"firm": r.get("Firm"), "action": r.get("Action"), "to": to, "from": frm,
                              "target": float(cur) if cur else None, "prior_target": float(prior) if prior else None},
                    )
                )
            )
        return out

    # ---------------------------------------------------------------- intraday
    async def intraday(self, prev_close: float, close: float, sector_etf: str | None, sector_ret: float | None) -> dict:
        """Fetch the event day's 5-minute path, record a summary as evidence,
        and annotate same-day news with how much of the move had already
        happened when it was published. Returns the chart payload."""
        t, day = self.ctx.ticker, self.ctx.event_date
        bars = intra.scaled(await mkt.intraday(t, day), close)
        summ = intra.path_summary(bars, prev_close, close)
        series = {t: [{"t": ts.isoformat(), "pct": round(float(p / prev_close - 1), 5)} for ts, p in bars.items()]}
        etf_note = ""
        if sector_etf and sector_ret is not None:
            try:
                eb = await mkt.intraday(sector_etf, day)
                e_prev = float(eb.iloc[-1]) / (1 + sector_ret)
                series[sector_etf] = [{"t": ts.isoformat(), "pct": round(float(p / e_prev - 1), 5)} for ts, p in eb.items()]
                etf_note = f" {sector_etf} (sector) closed {sector_ret:+.1%}."
            except (DataUnavailable, BudgetExceeded) as exc:
                self.gaps.append(f"Sector intraday unavailable: {exc}")

        anchors = []
        for e in list(self.items.values()) + list(self._existing.values()):
            if e["kind"] not in ("news", "analyst", "sec_filing") or e["timing"] != "during" or not e.get("published_at") or e.get("date_only"):
                continue
            ts = datetime.fromisoformat(e["published_at"])
            p = intra.price_at(bars, ts, prev_close)
            if p is None:
                continue
            share = intra.share_of_move(p, prev_close, close)
            e.setdefault("meta", {})
            e["meta"]["price_move_at_publish"] = round(p / prev_close - 1, 5)
            e["meta"]["share_of_move_at_publish"] = None if share is None else round(share, 3)
            anchors.append((ts, e, p, share))
        anchors.sort(key=lambda x: x[0])
        by = ", ".join(f"{k} {v:.0%}" for k, v in summ["share_by"].items() if v is not None)
        lines = [
            f"First 5 minutes {summ['open_pct']:+.1%}; low {summ['low_pct']:+.1%} at {summ['low_at']} ET; "
            f"high {summ['high_pct']:+.1%} at {summ['high_at']} ET; close {summ['close_pct']:+.1%}.{etf_note}",
            f"Share of the day's move completed by: {by}." if by else "",
        ]
        for ts, e, p, share in anchors[:8]:
            when = "before the open" if ts.astimezone(ET).date() < day or ts.astimezone(ET).time() <= intra.OPEN else f"at {ts.astimezone(ET):%-I:%M %p} ET"
            done = f" ({share:.0%} of the day's move had already happened)" if share is not None else ""
            lines.append(f"When {e['id']} was published {when}, {t} was {p / prev_close - 1:+.1%}{done}.")
        self.add(
            Evidence(
                id=evidence_id("intraday", t, day.isoformat()),
                kind="computed",
                title=f"{t} intraday path on {day}: low {summ['low_pct']:+.1%} at {summ['low_at']} ET, close {summ['close_pct']:+.1%}",
                source="Market Detective, from Yahoo Finance 5-minute bars",
                url=f"https://finance.yahoo.com/chart/{t}",
                published_at=market_close_dt(day).isoformat(),
                retrieved_at=now_iso(),
                timing="during",
                snippet=" ".join(x for x in lines if x),
                meta={"summary": summ},
            )
        )
        return {"date": day.isoformat(), "prev_close": prev_close, "close": close, "series": series, "summary": summ}

    # ---------------------------------------------------------------- macro
    async def macro(self, series: list[str] | None = None) -> list[dict]:
        fred = self.ctx.deps.fred
        if not fred.available:
            raise DataUnavailable("FRED_API_KEY not configured")
        out = []
        for sid in series or ["DGS10", "DGS2", "VIXCLS"]:
            try:
                m = await fred.series_move(sid, self.ctx.event_date)
            except DataUnavailable as exc:
                self.gaps.append(str(exc))
                continue
            unit = "bp" if m.unit == "pct" else "pts"
            chg = m.change * 100 if m.unit == "pct" else m.change
            close_dt = datetime.combine(self.ctx.event_date, datetime.min.time(), tzinfo=ET).replace(hour=16)
            out.append(
                self.add(
                    Evidence(
                        id=evidence_id("fred", sid, m.date),
                        kind="macro_data",
                        title=f"{m.label} {chg:+.0f}{unit} on {m.date} (to {m.value:.2f})",
                        source="FRED",
                        url=f"https://fred.stlouisfed.org/series/{sid}",
                        published_at=close_dt.isoformat(),
                        retrieved_at=now_iso(),
                        timing=self.timing(close_dt),
                        snippet=f"{m.label}: {m.prev_value:.2f} -> {m.value:.2f}.",
                        meta={"series": sid, "change": m.change},
                    )
                )
            )
        start = self.ctx.event_date - timedelta(days=3)
        for rel in await fred.releases_between(start, self.ctx.event_date):
            out.append(
                self.add(
                    Evidence(
                        id=evidence_id("release", rel.name, rel.date.isoformat()),
                        kind="macro_data",
                        title=f"Scheduled release: {rel.name}",
                        source="FRED release calendar",
                        url="https://fred.stlouisfed.org/releases/calendar",
                        published_at=rel.published_at.isoformat() if rel.published_at else rel.date.isoformat(),
                        date_only=rel.published_at is None,
                        retrieved_at=now_iso(),
                        timing=self.timing(rel.published_at, date_only=False) if rel.published_at else "unknown",
                        snippet=f"{rel.name} released {rel.date} (customary time).",
                    )
                )
            )
        return out


# --------------------------------------------------------------------------
# Rendering evidence for prompts. Retrieved text is fenced as untrusted data.
# --------------------------------------------------------------------------
def render_evidence(items: list[dict] | dict[str, dict], snippet_chars: int = 400) -> str:
    if isinstance(items, dict):
        items = list(items.values())
    if not items:
        return "(no evidence)"
    lines = []
    for e in sorted(items, key=lambda x: x.get("published_at") or ""):
        flags = f" flags={','.join(e['flags'])}" if e.get("flags") else ""
        lines.append(
            f'<evidence id="{e["id"]}" kind="{e["kind"]}" source="{e["source"]}" '
            f'published="{e.get("published_at") or "unknown"}" timing="{e["timing"]}"{flags}>\n'
            f"{e['title']}\n{(e.get('snippet') or '')[:snippet_chars]}\n</evidence>"
        )
    return "\n".join(lines)


def market_close_dt(d: date) -> datetime:
    return datetime.combine(d, datetime.min.time(), tzinfo=ET).replace(hour=16)


def _num(x) -> bool:
    try:
        return x is not None and float(x) == float(x)
    except (TypeError, ValueError):
        return False
