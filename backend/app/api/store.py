"""Application database: case index, follow-up conversations and the watchlist.
(Graph state itself lives in the LangGraph SQLite checkpointer.)"""

from __future__ import annotations

import json
from pathlib import Path

import aiosqlite

from app.agents.evidence import now_iso

SCHEMA = """
CREATE TABLE IF NOT EXISTS cases (
  id TEXT PRIMARY KEY, query TEXT, ticker TEXT, company TEXT, status TEXT,
  created_at TEXT, updated_at TEXT, headline TEXT, verdict TEXT,
  event_date TEXT, ret REAL, z REAL, error TEXT
);
CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT, case_id TEXT, role TEXT, content TEXT,
  cited TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS watchlist (ticker TEXT PRIMARY KEY, added_at TEXT);
"""

DEFAULT_WATCHLIST = ["NVDA", "AAPL", "MSFT", "AMZN", "TSLA", "META", "AMD", "SPY"]


class Store:
    def __init__(self, path: Path):
        self._path = path

    async def init(self) -> None:
        async with aiosqlite.connect(self._path) as db:
            await db.executescript(SCHEMA)
            cur = await db.execute("SELECT COUNT(*) FROM watchlist")
            if (await cur.fetchone())[0] == 0:
                await db.executemany(
                    "INSERT INTO watchlist VALUES (?, ?)", [(t, now_iso()) for t in DEFAULT_WATCHLIST]
                )
            await db.commit()

    async def create_case(self, case_id: str, query: str) -> None:
        async with aiosqlite.connect(self._path) as db:
            await db.execute(
                "INSERT INTO cases (id, query, status, created_at, updated_at) VALUES (?, ?, 'queued', ?, ?)",
                (case_id, query, now_iso(), now_iso()),
            )
            await db.commit()

    async def update_case(self, case_id: str, **fields) -> None:
        if not fields:
            return
        fields["updated_at"] = now_iso()
        cols = ", ".join(f"{k}=?" for k in fields)
        async with aiosqlite.connect(self._path) as db:
            await db.execute(f"UPDATE cases SET {cols} WHERE id=?", (*fields.values(), case_id))
            await db.commit()

    async def list_cases(self, limit: int = 50) -> list[dict]:
        async with aiosqlite.connect(self._path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT * FROM cases ORDER BY created_at DESC LIMIT ?", (limit,))
            return [dict(r) for r in await cur.fetchall()]

    async def get_case(self, case_id: str) -> dict | None:
        async with aiosqlite.connect(self._path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT * FROM cases WHERE id=?", (case_id,))
            r = await cur.fetchone()
            return dict(r) if r else None

    async def add_message(self, case_id: str, role: str, content: str, cited: list[str] | None = None) -> dict:
        msg = {"case_id": case_id, "role": role, "content": content, "cited": cited or [], "created_at": now_iso()}
        async with aiosqlite.connect(self._path) as db:
            await db.execute(
                "INSERT INTO messages (case_id, role, content, cited, created_at) VALUES (?, ?, ?, ?, ?)",
                (case_id, role, content, json.dumps(cited or []), msg["created_at"]),
            )
            await db.commit()
        return msg

    async def messages(self, case_id: str) -> list[dict]:
        async with aiosqlite.connect(self._path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT * FROM messages WHERE case_id=? ORDER BY id", (case_id,))
            return [{**dict(r), "cited": json.loads(r["cited"] or "[]")} for r in await cur.fetchall()]

    async def watchlist(self) -> list[str]:
        async with aiosqlite.connect(self._path) as db:
            cur = await db.execute("SELECT ticker FROM watchlist ORDER BY added_at, ticker")
            return [r[0] for r in await cur.fetchall()]

    async def add_watch(self, ticker: str) -> None:
        async with aiosqlite.connect(self._path) as db:
            await db.execute("INSERT OR IGNORE INTO watchlist VALUES (?, ?)", (ticker.upper(), now_iso()))
            await db.commit()

    async def remove_watch(self, ticker: str) -> None:
        async with aiosqlite.connect(self._path) as db:
            await db.execute("DELETE FROM watchlist WHERE ticker=?", (ticker.upper(),))
            await db.commit()
