"""Financial news.

Sources, merged and de-duplicated:
* Google News RSS - keyless, date-bounded (``after:``/``before:``) search with
  publication timestamps, covering Reuters, CNBC, Barron's, etc. This is what
  makes same-day coverage of a move available.
* NewsAPI - when ``NEWSAPI_KEY`` is set (its free tier lags ~24h and covers ~1 month).
* Yahoo Finance headlines - last resort; only the most recent stories.
"""

from __future__ import annotations

import asyncio
import html
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

from app.data.http import CachedHttp, DataUnavailable, current_budget

NEWSAPI_URL = "https://newsapi.org/v2/everything"
GOOGLE_NEWS_RSS = "https://news.google.com/rss/search"


@dataclass
class Article:
    title: str
    source: str
    url: str | None
    published_at: datetime | None
    description: str


class News:
    def __init__(self, http: CachedHttp, newsapi_key: str | None):
        self._http = http
        self._key = newsapi_key

    @property
    def provider(self) -> str:
        return "Google News + NewsAPI" if self._key else "Google News"

    async def search(
        self, query: str, start: date, end: date, *, ticker: str | None = None, limit: int = 15
    ) -> list[Article]:
        tasks = [self._google(query, start, end, limit)]
        if self._key:
            tasks.append(self._newsapi(query, start, end, limit))
        results = await asyncio.gather(*tasks, return_exceptions=True)
        merged: list[Article] = []
        for r in results:
            if isinstance(r, list):
                merged.extend(r)
        if not merged and ticker:
            merged = await self._yfinance(ticker, start, end, limit)
        if not merged and all(isinstance(r, Exception) for r in results):
            raise DataUnavailable(f"News search failed: {results[0]}")
        return _dedupe(merged)[:limit]

    async def _google(self, query: str, start: date, end: date, limit: int) -> list[Article]:
        q = f"{query} after:{start.isoformat()} before:{(end + timedelta(days=1)).isoformat()}"
        text = await self._http.get_text(
            GOOGLE_NEWS_RSS,
            source="google_news",
            params={"q": q, "hl": "en-US", "gl": "US", "ceid": "US:en"},
            headers={"User-Agent": "Mozilla/5.0 (research prototype)"},
            ttl=2 * 3600,
        )
        try:
            root = ET.fromstring(text)
        except ET.ParseError as exc:
            raise DataUnavailable("Google News returned invalid RSS") from exc
        out = []
        for it in root.findall(".//item")[: limit * 2]:
            publisher = (it.findtext("source") or "").strip() or "Google News"
            title = (it.findtext("title") or "").strip()
            suffix = f" - {publisher}"
            if title.endswith(suffix):  # titles carry " - Publisher"
                title = title[: -len(suffix)]
            try:
                pub = parsedate_to_datetime(it.findtext("pubDate") or "")
            except (TypeError, ValueError):
                pub = None
            desc = html.unescape(re.sub(r"<[^>]+>", " ", it.findtext("description") or ""))
            desc = re.sub(r"\s+", " ", desc).strip()
            if desc.startswith(title):  # the RSS description usually repeats the title
                desc = ""
            out.append(Article(title=title, source=publisher, url=it.findtext("link"), published_at=pub,
                               description=desc))
        return out

    async def _newsapi(self, query: str, start: date, end: date, limit: int) -> list[Article]:
        data = await self._http.get_json(
            NEWSAPI_URL,
            source="newsapi",
            params={
                "q": query,
                "from": start.isoformat(),
                "to": (end + timedelta(days=1)).isoformat(),
                "language": "en",
                "sortBy": "relevancy",
                "pageSize": min(limit, 50),
                "searchIn": "title,description",
            },
            headers={"X-Api-Key": self._key},
            ttl=2 * 3600,
        )
        if data.get("status") != "ok":
            raise DataUnavailable(f"NewsAPI: {data.get('message', 'error')[:160]}")
        out = []
        for a in data.get("articles", []):
            out.append(
                Article(
                    title=(a.get("title") or "").strip(),
                    source=(a.get("source") or {}).get("name") or "unknown",
                    url=a.get("url"),
                    published_at=_parse_iso(a.get("publishedAt")),
                    description=(a.get("description") or "").strip(),
                )
            )
        return [a for a in out if a.title and a.title != "[Removed]"]

    async def _yfinance(self, ticker: str, start: date, end: date, limit: int) -> list[Article]:
        budget = current_budget.get()
        if budget is not None:
            budget.charge("yfinance")

        def _fetch():
            import yfinance as yf

            try:
                return yf.Ticker(ticker).get_news(count=50) or []
            except Exception:
                return []

        items = await asyncio.to_thread(_fetch)
        out = []
        for it in items:
            c = it.get("content", it)
            pub = _parse_iso(c.get("pubDate") or c.get("displayTime"))
            if pub is None and it.get("providerPublishTime"):
                pub = datetime.fromtimestamp(it["providerPublishTime"], tz=timezone.utc)
            if pub and not (start <= pub.date() <= end + timedelta(days=1)):
                continue
            url = (c.get("canonicalUrl") or {}).get("url") or c.get("link")
            out.append(
                Article(
                    title=c.get("title", ""),
                    source=(c.get("provider") or {}).get("displayName") or c.get("publisher") or "Yahoo Finance",
                    url=url,
                    published_at=pub,
                    description=c.get("summary") or "",
                )
            )
        return out[:limit]


def _parse_iso(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _dedupe(articles: list[Article]) -> list[Article]:
    """Drop repeats of the same headline (syndicated copies across sources)."""
    seen, out = set(), []
    for a in articles:
        key = re.sub(r"[^a-z0-9]+", " ", a.title.lower()).strip()[:80]
        if key and key not in seen:
            seen.add(key)
            out.append(a)
    return out
