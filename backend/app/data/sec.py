"""SEC EDGAR: ticker/CIK resolution, filing lists with acceptance timestamps, and
filing text for semantic retrieval."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from difflib import SequenceMatcher
from zoneinfo import ZoneInfo

import warnings

from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

from app.data.http import CachedHttp, DataUnavailable

ET = ZoneInfo("America/New_York")
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{doc}"
INDEX_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/index.json"

RELEVANT_FORMS = {"8-K", "8-K/A", "10-Q", "10-K", "6-K", "SC 13D", "S-3", "424B5", "DEF 14A"}

# Item codes on Form 8-K -> human readable (subset that commonly moves prices).
EIGHT_K_ITEMS = {
    "1.01": "Entry into a material agreement",
    "1.02": "Termination of a material agreement",
    "1.03": "Bankruptcy or receivership",
    "2.01": "Completion of acquisition or disposition",
    "2.02": "Results of operations (earnings)",
    "2.05": "Exit or restructuring costs",
    "2.06": "Material impairments",
    "3.01": "Delisting notice",
    "4.02": "Non-reliance on prior financial statements",
    "5.02": "Officer/director departure or appointment",
    "7.01": "Regulation FD disclosure",
    "8.01": "Other events",
    "9.01": "Financial statements and exhibits",
}


@dataclass
class Company:
    ticker: str
    cik: int
    title: str


@dataclass
class Filing:
    form: str
    accession: str
    filing_date: str
    accepted_at: datetime | None  # Eastern time
    primary_document: str
    items: list[str]
    cik: int

    @property
    def url(self) -> str:
        return ARCHIVE_URL.format(
            cik=self.cik, acc=self.accession.replace("-", ""), doc=self.primary_document
        )

    @property
    def description(self) -> str:
        if self.form.startswith("8-K") and self.items:
            names = [EIGHT_K_ITEMS.get(i, f"Item {i}") for i in self.items if i != "9.01"]
            return f"Form {self.form}: " + "; ".join(names or ["exhibits"])
        return f"Form {self.form}"


class SecEdgar:
    def __init__(self, http: CachedHttp, user_agent: str):
        self._http = http
        self._headers = {"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"}

    async def _companies(self) -> list[Company]:
        data = await self._http.get_json(
            TICKERS_URL, source="sec_edgar", headers=self._headers, ttl=24 * 3600
        )
        return [
            Company(ticker=v["ticker"].upper(), cik=int(v["cik_str"]), title=v["title"])
            for v in data.values()
        ]

    async def resolve(self, query: str) -> list[Company]:
        """Resolve a ticker or company name. Returns exact ticker match first,
        otherwise up to five name matches (ambiguous queries return several)."""
        q = query.strip()
        companies = await self._companies()
        exact = [c for c in companies if c.ticker == q.upper().replace(".", "-")]
        if exact:
            return exact[:1]
        ql = q.lower()
        scored = []
        for c in companies:
            title = c.title.lower()
            if ql in title:
                score = 1.0 + (0.5 if title.startswith(ql) else 0)
            else:
                score = SequenceMatcher(None, ql, title).ratio()
            if score > 0.75:
                scored.append((score, c))
        scored.sort(key=lambda x: (-x[0], len(x[1].title)))
        # Collapse share classes (GOOG/GOOGL) that share a CIK.
        seen, out = set(), []
        for _, c in scored:
            if c.cik not in seen:
                seen.add(c.cik)
                out.append(c)
        return out[:5]

    async def filings(self, cik: int, start: date, end: date) -> list[Filing]:
        data = await self._http.get_json(
            SUBMISSIONS_URL.format(cik=cik), source="sec_edgar", headers=self._headers, ttl=3600
        )
        recent = data.get("filings", {}).get("recent", {})
        out: list[Filing] = []
        for i, form in enumerate(recent.get("form", [])):
            fdate = recent["filingDate"][i]
            if not (start.isoformat() <= fdate <= end.isoformat()):
                continue
            if form not in RELEVANT_FORMS:
                continue
            accepted = recent.get("acceptanceDateTime", [None] * (i + 1))[i]
            out.append(
                Filing(
                    form=form,
                    accession=recent["accessionNumber"][i],
                    filing_date=fdate,
                    accepted_at=_parse_acceptance(accepted),
                    primary_document=recent["primaryDocument"][i],
                    items=[s.strip() for s in (recent.get("items", [""] * (i + 1))[i] or "").split(",") if s.strip()],
                    cik=cik,
                )
            )
        return out

    async def filing_text(self, filing: Filing, max_chars: int = 400_000) -> tuple[str, str]:
        """Return (text, url). For 8-Ks the press-release exhibit (EX-99.x) is
        where earnings numbers and guidance live, so it is appended."""
        texts, url = [], filing.url
        texts.append(await self._html_text(filing.url))
        if filing.form.startswith("8-K") or filing.form == "6-K":
            try:
                idx = await self._http.get_json(
                    INDEX_URL.format(cik=filing.cik, acc=filing.accession.replace("-", "")),
                    source="sec_edgar",
                    headers=self._headers,
                    ttl=7 * 24 * 3600,
                )
                # Exhibit names vary by filer (ex99-1.htm, q3fy27pr.htm, ...), so take the
                # largest HTML documents other than the cover page and XBRL viewer pages:
                # typically the press release and CFO commentary.
                cands = []
                for item in idx.get("directory", {}).get("item", []):
                    name = item.get("name", "")
                    if (
                        name.lower().endswith((".htm", ".html"))
                        and name != filing.primary_document
                        and not re.match(r"^R\d+\.htm$", name)
                        and "index" not in name.lower()
                    ):
                        size = int(item.get("size") or 0)
                        bonus = 10**7 if re.search(r"ex[-_]?99|pr\.htm|press|release", name, re.I) else 0
                        cands.append((size + bonus, name))
                for _, name in sorted(cands, reverse=True)[:2]:
                    ex_url = ARCHIVE_URL.format(
                        cik=filing.cik, acc=filing.accession.replace("-", ""), doc=name
                    )
                    texts.append(f"\n\n[EXHIBIT {name}]\n" + await self._html_text(ex_url))
                    if url == filing.url:
                        url = ex_url
            except DataUnavailable:
                pass
        return "\n".join(texts)[:max_chars], url

    async def _html_text(self, url: str) -> str:
        html = await self._http.get_text(url, source="sec_edgar", headers=self._headers, ttl=30 * 24 * 3600)
        soup = BeautifulSoup(html, "lxml")
        for tag in soup(["script", "style"]):
            tag.decompose()
        # Inline XBRL hides metadata in <ix:header>; drop it.
        for tag in soup.find_all(re.compile(r"^ix:header$")):
            tag.decompose()
        text = soup.get_text("\n")
        return re.sub(r"\n\s*\n+", "\n\n", text)


def _parse_acceptance(raw: str | None) -> datetime | None:
    """EDGAR's ``acceptanceDateTime`` is UTC (e.g. NVIDIA's Q3 8-K accepted
    ``21:20:51Z`` = 4:20 PM ET, matching the filing index). Returned in ET so
    the timing check compares against the 4:00 PM ET close correctly."""
    if not raw:
        return None
    try:
        naive = datetime.strptime(raw[:19], "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return None
    return naive.replace(tzinfo=timezone.utc).astimezone(ET)
