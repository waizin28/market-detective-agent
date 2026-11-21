"""Shared HTTP plumbing: a small SQLite response cache and a per-case API budget.

Every external call goes through ``CachedHttp.get_json/get_text`` so that
(1) repeated investigations reuse documents instead of re-downloading them and
(2) each case has a hard cap on outbound calls (runtime guardrail, Week 6).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
import time
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path

import httpx
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)


class BudgetExceeded(RuntimeError):
    """Raised when a case exceeds its configured number of external API calls."""


class DataUnavailable(RuntimeError):
    """Raised when a data source cannot provide the requested data."""


@dataclass
class ApiBudget:
    limit: int
    used: int = 0
    by_source: dict[str, int] = field(default_factory=dict)

    def charge(self, source: str) -> None:
        if self.used >= self.limit:
            raise BudgetExceeded(f"API budget of {self.limit} calls exhausted")
        self.used += 1
        self.by_source[source] = self.by_source.get(source, 0) + 1


# The budget for the case currently being investigated. Set by the graph runner;
# when unset (e.g. the watchlist scan) calls are unmetered.
current_budget: ContextVar[ApiBudget | None] = ContextVar("current_budget", default=None)


class _SqliteCache:
    def __init__(self, path: Path):
        self._path = path
        with sqlite3.connect(path) as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS http_cache (key TEXT PRIMARY KEY, body TEXT, ts REAL)"
            )

    def get(self, key: str, ttl: float) -> str | None:
        with sqlite3.connect(self._path) as db:
            row = db.execute("SELECT body, ts FROM http_cache WHERE key=?", (key,)).fetchone()
        if row and time.time() - row[1] < ttl:
            return row[0]
        return None

    def put(self, key: str, body: str) -> None:
        with sqlite3.connect(self._path) as db:
            db.execute(
                "INSERT OR REPLACE INTO http_cache (key, body, ts) VALUES (?, ?, ?)",
                (key, body, time.time()),
            )


class CachedHttp:
    def __init__(self, cache_path: Path, timeout: float = 30.0):
        self._cache = _SqliteCache(cache_path)
        self._client = httpx.AsyncClient(timeout=timeout, follow_redirects=True)
        self._lock = asyncio.Lock()

    async def aclose(self) -> None:
        await self._client.aclose()

    @staticmethod
    def _key(url: str, params: dict | None, headers: dict | None) -> str:
        # Secrets in params (api keys) are hashed, never stored in plain text.
        raw = json.dumps([url, sorted((params or {}).items())], default=str)
        return hashlib.sha256(raw.encode()).hexdigest()

    async def get_text(
        self,
        url: str,
        *,
        source: str,
        params: dict | None = None,
        headers: dict | None = None,
        ttl: float = 6 * 3600,
    ) -> str:
        key = self._key(url, params, headers)
        cached = await asyncio.to_thread(self._cache.get, key, ttl)
        if cached is not None:
            return cached

        budget = current_budget.get()
        if budget is not None:
            budget.charge(source)

        # Bounded retries: a failed request consumes budget and cannot loop forever.
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=0.5, max=4),
            retry=retry_if_exception_type((httpx.TransportError, _Retryable)),
            reraise=True,
        ):
            with attempt:
                resp = await self._client.get(url, params=params, headers=headers)
                if resp.status_code in (429, 500, 502, 503, 504):
                    raise _Retryable(f"{source} HTTP {resp.status_code}")
                if resp.status_code >= 400:
                    raise DataUnavailable(f"{source} HTTP {resp.status_code} for {url}")
        body = resp.text
        await asyncio.to_thread(self._cache.put, key, body)
        return body

    async def get_json(self, url: str, **kwargs) -> dict | list:
        text = await self.get_text(url, **kwargs)
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise DataUnavailable(f"{kwargs.get('source')} returned non-JSON") from exc


class _Retryable(Exception):
    pass
