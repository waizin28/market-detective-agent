"""FastAPI server for the Market Detective UI.

Run: ``uv run uvicorn app.api.main:app --reload --port 8000`` from backend/.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from contextlib import asynccontextmanager
from datetime import date, timedelta

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from app.analysis.anomaly import compute_anomaly
from app.api.runs import RunManager
from app.api.store import Store
from app.config import get_settings
from app.data.http import DataUnavailable
from app.graph.builder import build_graph
from app.graph.deps import Deps

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    deps = Deps.build(settings)
    store = Store(settings.app_db)
    await store.init()
    async with AsyncSqliteSaver.from_conn_string(str(settings.checkpoint_db)) as saver:
        graph = build_graph(saver)
        app.state.settings = settings
        app.state.deps = deps
        app.state.store = store
        app.state.runs = RunManager(graph, deps, store)
        yield
    await deps.http.aclose()


app = FastAPI(title="Market Detective", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
class InvestigateRequest(BaseModel):
    query: str = Field(min_length=1, max_length=80, description="Ticker or company name")
    event_date: date | None = Field(None, description="Trading day to investigate; default = latest completed trading day")
    peers: list[str] | None = Field(None, max_length=3)
    force: bool = Field(False, description="Investigate even if the move is within the normal range")


class ResumeRequest(BaseModel):
    value: str = Field(min_length=1, max_length=20)


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1000)


class WatchRequest(BaseModel):
    ticker: str = Field(min_length=1, max_length=12, pattern=r"^[A-Za-z0-9.\-^]+$")


# ---------------------------------------------------------------------------
@app.get("/api/config")
async def config():
    s = app.state.settings
    return {
        "model": s.llm_model,
        "keys": {
            "openai": bool(s.openai_api_key),
            "alpha_vantage": bool(s.alpha_vantage_api_key),
            "newsapi": bool(s.newsapi_key),
            "fred": bool(s.fred_api_key),
            # Placeholder values (default or the .env.example template) don't count.
            "sec_user_agent": not any(
                p in s.sec_user_agent for p in ("set SEC_USER_AGENT", "example.com")
            ),
        },
        "limits": {
            "beam_width": s.beam_width,
            "max_rounds": s.max_rounds,
            "max_evidence_checks": s.max_evidence_checks,
            "z_threshold": s.anomaly_z_threshold,
            "lookback_days": s.anomaly_lookback_days,
        },
    }


@app.post("/api/cases", status_code=201)
async def create_case(req: InvestigateRequest):
    if not app.state.settings.openai_api_key:
        raise HTTPException(400, "OPENAI_API_KEY is not configured on the server (backend/.env)")
    if req.event_date and req.event_date > date.today():
        raise HTTPException(400, "event_date is in the future")
    case_id = uuid.uuid4().hex[:12]
    await app.state.store.create_case(case_id, req.query)
    app.state.runs.start(
        case_id,
        {
            "case_id": case_id,
            "query": req.query,
            "requested_date": req.event_date.isoformat() if req.event_date else None,
            "peers_override": [p.upper() for p in req.peers] if req.peers else None,
            "force": req.force,
            "evidence": {},
            "branches": {},
            "findings": [],
            "gaps": [],
            "events": [],
            "checks_used": 0,
            "status": "starting",
        },
    )
    return {"case_id": case_id}


@app.get("/api/cases")
async def list_cases():
    return await app.state.store.list_cases()


@app.get("/api/cases/{case_id}")
async def get_case(case_id: str):
    view = await app.state.runs.case_view(case_id)
    if view is None:
        raise HTTPException(404, "case not found")
    view["messages"] = await app.state.store.messages(case_id)
    return view


@app.get("/api/cases/{case_id}/events")
async def case_events(case_id: str, since: int = 0):
    if await app.state.store.get_case(case_id) is None:
        raise HTTPException(404, "case not found")

    async def gen():
        async for ev in app.state.runs.stream(case_id, since):
            yield {"event": ev.get("type", "message"), "data": json.dumps(ev, default=str)}

    return EventSourceResponse(gen())


@app.post("/api/cases/{case_id}/resume")
async def resume_case(case_id: str, req: ResumeRequest):
    view = await app.state.runs.case_view(case_id)
    if not view or not view.get("interrupt"):
        raise HTTPException(409, "case is not waiting for input")
    app.state.runs.start(case_id, Command(resume=req.value.strip().upper()))
    return {"ok": True}


@app.post("/api/cases/{case_id}/ask")
async def ask(case_id: str, req: AskRequest):
    snap = await app.state.runs.graph.aget_state(app.state.runs.config(case_id))
    state = snap.values
    if not state or not state.get("report"):
        raise HTTPException(409, "the case file isn't ready yet")
    store = app.state.store
    history = await store.messages(case_id)
    await store.add_message(case_id, "user", req.question)
    try:
        ans = await app.state.deps.roles.answer(state, req.question, history)
    except Exception as exc:
        raise HTTPException(502, f"follow-up failed: {exc}") from exc
    valid = [i for i in ans.cited_evidence_ids if i in state["evidence"]]
    return await store.add_message(case_id, "assistant", ans.answer, valid)


# ---------------------------------------------------------------------------
# Watchlist scan: which stocks are acting unusually right now (no LLM)
# ---------------------------------------------------------------------------
@app.get("/api/watchlist")
async def get_watchlist():
    return await app.state.store.watchlist()


@app.post("/api/watchlist", status_code=201)
async def add_watch(req: WatchRequest):
    await app.state.store.add_watch(req.ticker)
    return await app.state.store.watchlist()


@app.delete("/api/watchlist/{ticker}")
async def remove_watch(ticker: str):
    await app.state.store.remove_watch(ticker)
    return await app.state.store.watchlist()


@app.get("/api/scan")
async def scan(tickers: str | None = None):
    s, deps = app.state.settings, app.state.deps
    symbols = [t.strip().upper() for t in tickers.split(",")] if tickers else await app.state.store.watchlist()
    symbols = [t for t in symbols if t][:25]
    end = date.today()
    start = end - timedelta(days=int((s.anomaly_lookback_days + s.anomaly_scan_days) * 1.6) + 30)
    try:
        spy = await deps.market.history("SPY", start, end)
    except DataUnavailable:
        spy = None
    sem = asyncio.Semaphore(6)

    async def one(t: str) -> dict:
        async with sem:
            try:
                df = await deps.market.history(t, start, end)
                bench = {"SPY": ("market", spy)} if t != "SPY" and spy is not None else {}
                a = compute_anomaly(
                    t, df, bench, lookback=s.anomaly_lookback_days, z_threshold=s.anomaly_z_threshold,
                    scan_days=s.anomaly_scan_days,
                    default="largest",
                )
                last = df["close"].iloc[-1]
                last_ret = float(df["close"].pct_change().iloc[-1])
                return {
                    "ticker": t,
                    "last_close": float(last),
                    "last_date": df.index[-1].date().isoformat(),
                    "last_ret": last_ret,
                    "event_date": a.event_date,
                    "event_ret": a.ret,
                    "z_score": a.z_score,
                    "flagged": a.flagged,
                    # Only SPY is fetched here, so scope is relative to the market only;
                    # the full investigation adds sector and peer benchmarks.
                    "vs_market": "with_market" if a.scope == "market_wide" else (
                        "beyond_market" if t != "SPY" and spy is not None else "n/a"),
                    "volume_ratio": a.volume_ratio,
                    "spark": [round(float(x), 2) for x in df["close"].tail(40)],
                }
            except Exception as exc:
                return {"ticker": t, "error": str(exc)[:200]}

    results = await asyncio.gather(*(one(t) for t in symbols))
    return sorted(results, key=lambda r: -abs(r.get("z_score") or 0))
