# Market Detective architecture

Four views: the system as a whole, the LangGraph investigation, one request from start to
finish, and who decides what (LLM vs. Python).

## 1. System overview

```mermaid
flowchart LR
    subgraph UI["React dashboard (Vite, :5173)"]
        Search["Search bar + event date"]
        CaseUI["Case page: verdict, hypothesis tree,<br/>lead-up timeline, price chart, follow-up Q&A"]
        Watch["Watchlist scanner"]
        Hist["Research history + settings"]
    end

    subgraph API["FastAPI backend (:8000)"]
        REST["REST: /api/cases, /resume, /ask,<br/>/scan, /watchlist, /config"]
        SSE["SSE event stream<br/>(replayable, seq-numbered)"]
        RM["RunManager: background runs,<br/>API budget, timeout"]
        Store[("app.sqlite<br/>cases, messages, watchlist")]
    end

    subgraph Graph["LangGraph StateGraph"]
        G["Investigation graph<br/>(see section 2)"]
        CP[("checkpoints.sqlite<br/>AsyncSqliteSaver")]
    end

    subgraph Agents["LangChain agent roles (OpenAI)"]
        Coord["Coordinator"]
        Analyst["Hypothesis analyst"]
        Researcher["Evidence researcher<br/>(ReAct, read-only tools)"]
        Critic["Critic"]
    end

    subgraph Py["Deterministic Python"]
        Anom["Anomaly stats<br/>z-score, beta, scope"]
        Timing["Timing rule<br/>public before 4 PM ET close?"]
        Score["Rubric scoring, beam,<br/>verdict rules"]
        Retr["Filing retrieval<br/>400-token chunks, MiniLM, top-5"]
    end

    subgraph Data["External data (read-only, HTTP-cached)"]
        Mkt["Alpha Vantage / Yahoo Finance"]
        SEC["SEC EDGAR filings"]
        News["Google News / NewsAPI / Yahoo"]
        FRED["FRED macro"]
    end

    Search --> REST
    Watch --> REST
    Hist --> REST
    REST --> RM
    RM --> G
    G <--> CP
    RM --> SSE
    SSE --> CaseUI
    RM --> Store
    G --> Agents
    G --> Py
    Researcher --> Retr
    Agents --> Data
    Py --> Data
```

## 2. Investigation graph (LangGraph)

```mermaid
flowchart TD
    S([Start]) --> A["resolve_case<br/>coordinator: confirm ticker, pick peers + sector ETF"]
    A -->|ambiguous company name| H{{"interrupt: ask the user"}}
    H -->|user picks| A
    A --> B["detect_anomaly (Python)<br/>picked day, default latest close<br/>z-score vs prior 60 trading days,<br/>SPY, sector ETF, peers"]
    B --> C["build_timeline (no LLM)<br/>SEC filings, a week of news,<br/>macro, peer returns"]
    C -->|"within ±2σ (normal day)"| N["normal-day brief<br/>1 coordinator call"]
    C -->|"z beyond ±2σ, or 'investigate anyway'"| D["propose_hypotheses<br/>analyst: 2-4 competing explanations"]
    D -->|Send fan-out| R["research_check x N (parallel)<br/>researcher ReAct agent, ≤4 tool calls"]
    R --> E["critique<br/>critic, one call per branch in parallel;<br/>Python re-checks timing + independence"]
    E --> F{"controller (Python)<br/>prune below 2/4, keep beam of 2"}
    F -->|after round 1| G["refine_branches<br/>analyst sharpens survivors"]
    G -->|Send fan-out| R
    F -->|"after round 2, a finalist could lead"| I["plan_challenges<br/>critic: try to disprove each finalist"]
    I -->|Send fan-out| R
    F -->|"round 3 done, 10 checks used,<br/>none left, or none could lead"| W["write_report<br/>verdict by rules, narrative by coordinator"]
    N --> X([Case file])
    W --> X
```

**Budgets:** ≤10 evidence checks per case, ≤4 tool calls per researcher, an API-call
budget, and a case timeout. **Leading explanation:** score ≥3/4 with a documented event,
public before the close, plus a completed challenge. Otherwise the verdict is "unresolved".

## 3. One investigation, end to end

```mermaid
sequenceDiagram
    actor U as User
    participant UI as React UI
    participant API as FastAPI
    participant RM as RunManager
    participant LG as LangGraph
    participant EXT as Data sources + OpenAI

    U->>UI: NVDA, 2026-11-20
    UI->>API: POST /api/cases
    API->>RM: start background run
    API-->>UI: case_id
    UI->>API: GET /api/cases/{id}/events (SSE)
    RM->>LG: astream(updates + custom)
    loop each node
        LG->>EXT: market data, filings, news, LLM calls
        LG-->>RM: progress + state snapshot
        RM-->>UI: SSE progress / snapshot
    end
    opt ambiguous company
        LG-->>RM: interrupt(candidates)
        RM-->>UI: SSE interrupt
        U->>UI: picks AAPL
        UI->>API: POST /resume
        API->>RM: Command(resume)
    end
    LG-->>RM: report
    RM-->>UI: SSE done
    U->>UI: follow-up question
    UI->>API: POST /ask (grounded in the case file)
```

## 4. What the LLM decides vs. what code enforces

| OpenAI agents | Deterministic Python |
|---|---|
| Pick peers, propose and refine hypotheses, plan challenges | Returns, z-scores, beta, abnormal return, move scope |
| Choose research tools, summarize findings | Evidence IDs, timestamps, before/during/after labels |
| Judge whether an event is documented and the prices fit | Timing rule: after-close items can't be the trigger |
| Write the "why it moved" text, summary and key-event headlines | Independence rule, price-fit guards, pruning, beam, budgets, final verdict |
