"""Financial news: NewsAPI (primary, per the design) with a yfinance headline fallback."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from app.data.http import CachedHttp, DataUnavailable, current_budget

NEWSAPI_URL = "https://newsapi.org/v2/everything"


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
        return "NewsAPI" if self._key else "Yahoo Finance headlines"

    async def search(
        self, query: str, start: date, end: date, *, ticker: str | None = None, limit: int = 15
    ) -> list[Article]:
        if self._key:
            return await self._newsapi(query, start, end, limit)
        if ticker:
            return await self._yfinance(ticker, start, end, limit)
        raise DataUnavailable("No news provider configured (set NEWSAPI_KEY)")

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
