"""Runs investigations in the background and fans their progress out to SSE clients.

Each case's events are kept in memory with a sequence number so a browser that
reconnects (or opens the case later) replays everything it missed.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from langgraph.types import Command

from app.data.http import ApiBudget, current_budget
from app.graph.deps import Deps

log = logging.getLogger(__name__)


@dataclass
class Run:
    case_id: str
    events: list[dict] = field(default_factory=list)
    cond: asyncio.Condition = field(default_factory=asyncio.Condition)
    task: asyncio.Task | None = None
    finished: bool = False
    budget: ApiBudget | None = None

    async def publish(self, event: dict) -> None:
        async with self.cond:
            event["seq"] = len(self.events)
            self.events.append(event)
            self.cond.notify_all()


class RunManager:
    def __init__(self, graph, deps: Deps, store):
        self.graph = graph
        self.deps = deps
        self.store = store
        self.runs: dict[str, Run] = {}

    @staticmethod
    def config(case_id: str) -> dict:
        return {"configurable": {"thread_id": case_id}, "recursion_limit": 60}

    # ------------------------------------------------------------------ views
    async def case_view(self, case_id: str) -> dict | None:
        snap = await self.graph.aget_state(self.config(case_id))
        v = snap.values
        if not v:
            return None
        interrupt = None
        for task in snap.tasks or []:
            for it in getattr(task, "interrupts", []) or []:
                interrupt = it.value
        run = self.runs.get(case_id)
        evidence = sorted((v.get("evidence") or {}).values(), key=lambda e: e.get("published_at") or "")
        branches = sorted((v.get("branches") or {}).values(), key=lambda b: b["order"])
        return {
            "case_id": case_id,
            "query": v.get("query"),
            "status": "waiting_for_input" if interrupt else v.get("status", "queued"),
            "error": v.get("error"),
            "ticker": v.get("ticker"),
            "company": v.get("company"),
            "profile": v.get("profile"),
            "sector_etf": v.get("sector_etf"),
            "peers": v.get("peers", []),
            "anomaly": v.get("anomaly"),
            "prices": v.get("prices", []),
            "benchmark_prices": v.get("benchmark_prices", {}),
            "intraday": v.get("intraday"),
            "upcoming": v.get("upcoming", []),
            "evidence": evidence,
            "branches": branches,
            "findings": v.get("findings", []),
            "gaps": list(dict.fromkeys(v.get("gaps", []))),
            "events": v.get("events", []),
            "round": v.get("round", 0),
            "checks_used": v.get("checks_used", 0),
            "max_checks": self.deps.settings.max_evidence_checks,
            "report": v.get("report"),
            "interrupt": interrupt,
            "api_calls": run.budget.used if run and run.budget else None,
            "running": bool(run and not run.finished),
        }

    # ------------------------------------------------------------------ runs
    def start(self, case_id: str, payload: dict | Command) -> Run:
        run = self.runs.get(case_id) or Run(case_id)
        run.finished = False
        self.runs[case_id] = run
        run.task = asyncio.create_task(self._execute(run, payload))
        return run

    async def _execute(self, run: Run, payload: Any) -> None:
        s = self.deps.settings
        run.budget = run.budget or ApiBudget(limit=s.max_api_calls_per_case)
        token = current_budget.set(run.budget)
        cfg = self.config(run.case_id)
        await self.store.update_case(run.case_id, status="running")
        try:
            async with asyncio.timeout(s.case_timeout_seconds):
                async for mode, chunk in self.graph.astream(
                    payload, cfg, context=self.deps, stream_mode=["custom", "updates"]
                ):
                    if mode == "custom":
                        await run.publish(chunk)
                        continue
                    if "__interrupt__" in chunk:
                        await run.publish({"type": "interrupt", "payload": chunk["__interrupt__"][0].value})
                    view = await self.case_view(run.case_id)
                    await run.publish({"type": "snapshot", "case": view})
            view = await self.case_view(run.case_id)
            await self._index(run.case_id, view)
            await run.publish({"type": "done", "status": view["status"] if view else "unknown"})
        except TimeoutError:
            await self._fail(run, f"Investigation exceeded the {s.case_timeout_seconds}s runtime limit")
        except Exception as exc:  # surfaced to the UI; partial state stays in the checkpoint
            log.exception("case %s failed", run.case_id)
            await self._fail(run, f"{type(exc).__name__}: {exc}")
        finally:
            current_budget.reset(token)
            run.finished = True
            async with run.cond:
                run.cond.notify_all()

    async def _fail(self, run: Run, message: str) -> None:
        try:
            await self.graph.aupdate_state(self.config(run.case_id), {"status": "error", "error": message})
        except Exception:  # no checkpoint yet (failed on the first step)
            log.warning("could not record error state for %s", run.case_id)
        await self.store.update_case(run.case_id, status="error", error=message)
        await run.publish({"type": "error", "message": message})
        await run.publish({"type": "snapshot", "case": await self.case_view(run.case_id)})

    async def _index(self, case_id: str, view: dict | None) -> None:
        if not view:
            return
        a, r = view.get("anomaly") or {}, view.get("report") or {}
        await self.store.update_case(
            case_id,
            status=view["status"],
            ticker=view.get("ticker"),
            company=view.get("company"),
            headline=r.get("headline"),
            verdict=(r.get("verdict") or {}).get("status"),
            event_date=a.get("event_date"),
            ret=a.get("ret"),
            z=a.get("z_score"),
        )

    # ------------------------------------------------------------------ streaming
    async def stream(self, case_id: str, since: int = 0):
        run = self.runs.get(case_id)
        if run is None:  # e.g. after a server restart: send the stored state once
            yield {"type": "snapshot", "case": await self.case_view(case_id), "seq": 0}
            yield {"type": "done", "seq": 1}
            return
        i = since
        while True:
            async with run.cond:
                while i >= len(run.events) and not run.finished:
                    try:
                        await asyncio.wait_for(run.cond.wait(), timeout=15)
                    except TimeoutError:
                        break
                pending = run.events[i:]
            if not pending:
                if run.finished:
                    return
                yield {"type": "ping"}
                continue
            for ev in pending:
                yield ev
            i += len(pending)
