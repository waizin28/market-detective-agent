# Market Detective architecture

Five views: the system as a whole, the LangGraph investigation, one request from start to
finish, who decides what (LLM vs. Python), and the researcher's tools.

## 1. System overview

```mermaid
flowchart TB
    subgraph UI["React dashboard"]
        Search["Search: ticker or company, date,<br/>optional peers, investigate anyway"]
        CaseUI["Case page: verdict, hypothesis tree,<br/>lead-up timeline, daily + intraday charts,<br/>upcoming dates, follow-up Q&A, printable case file"]
        Watch["Watchlist scanner"]
        Hist["Research history + settings"]
    end

    subgraph API["FastAPI backend"]
        Cases["/api/cases, /resume,<br/>/events (SSE)"]
        Ask["/ask"]
        Scan["/scan, /watchlist"]
        Meta["/api/cases list, /config"]
        RM["RunManager: background run per case,<br/>replayable seq-numbered events,<br/>120 API calls, 900 s timeout"]
        AppDB[("app.sqlite<br/>case index, Q&A, watchlist")]
    end

    subgraph Graph["LangGraph (section 2)"]
        G["Investigation graph"]
        CP[("checkpoints.sqlite")]
    end

    subgraph Roles["OpenAI roles (LangChain)"]
        Coord["Coordinator"]
        Analyst["Analyst"]
        Researcher["Researcher<br/>ReAct agent, 6 read-only tools"]
        Critic["Critic"]
    end

    subgraph Py["Deterministic Python"]
        Anom["Anomaly stats<br/>z-score, beta, scope"]
        Rules["Timing, rubric guards,<br/>beam, verdict rules"]
        Retr["Filing retrieval<br/>400-token chunks, MiniLM, top 5"]
    end

    subgraph Data["External data, read-only"]
        Mkt["Alpha Vantage / Yahoo Finance<br/>prices, intraday, earnings,<br/>analyst actions, calendar"]
        SEC["SEC EDGAR<br/>ticker lookup, filings"]
        News["Google News RSS<br/>NewsAPI / Yahoo"]
        FRED["FRED macro<br/>+ release dates"]
    end

    Search --> Cases
    CaseUI --> Cases
    CaseUI --> Ask
    Watch --> Scan
    Hist --> Meta
    Cases --> RM
    RM --> AppDB
    RM --> G
    G <--> CP
    Ask --> Coord
    Scan --> Anom
    G --> Roles
    G --> Py
    Researcher --> Retr
    Roles --> Data
    Py --> Data
```

## 2. Investigation graph (LangGraph)

```mermaid
flowchart TD
    S([Start]) --> A["resolve_case<br/>coordinator: SEC EDGAR lookup, sector ETF,<br/>up to 3 peers unless the user gave them"]
    A -->|"several matches, or not in EDGAR"| H{{"interrupt: ask the user"}}
    H -->|"resume: resolve_case re-runs with the ticker"| A
    A --> B["detect_anomaly (Python)<br/>picked day, default latest close<br/>z-score vs prior 60 trading days,<br/>SPY, sector ETF, peers, move scope"]
    B --> C["build_timeline (no LLM)<br/>SEC filings (30 days), the week's news + same-day coverage,<br/>earnings, analyst actions, 5-min intraday path,<br/>upcoming dates, FRED macro (if keyed)"]
    C -->|"within ±2σ (normal day)"| N["no_anomaly_report<br/>normal-day brief, 1 coordinator call"]
    C -->|"z beyond ±2σ, or 'investigate anyway'"| D["propose_hypotheses<br/>analyst: up to 4 competing explanations"]
    D -->|Send fan-out| R["research_check x N (parallel)<br/>researcher ReAct agent, ≤4 tool calls"]
    R --> E["critique<br/>critic, one call per branch in parallel;<br/>Python re-checks timing, price fit, independence"]
    E --> F{"controller (Python)<br/>prune below 2/4 or contradicted,<br/>keep beam of 2"}
    F -->|after round 1| G["refine_branches<br/>analyst sharpens survivors,<br/>reserves 1 check per branch for challenges"]
    G -->|Send fan-out| R
    F -->|"after round 2, a finalist could lead"| I["plan_challenges<br/>critic: try to disprove each finalist"]
    I -->|Send fan-out| R
    F -->|"round 3 done, 10 checks used,<br/>none left, or none could lead"| W["write_report<br/>verdict by rules, narrative by coordinator"]
    D & G & I -.->|"no checks left to run"| W
    N --> X([Case file])
    W --> X
```

**Budgets:** ≤10 evidence checks per case, ≤4 tool calls per researcher, 120 API calls and a
900 s timeout per case, 2 attempts per LLM node.

**Pruning:** below 2/4 or decisively contradicted; the beam keeps 2. If every branch fell short
only for missing evidence, the best 2 stay on provisionally before round 3.

**Leading explanation:** score ≥3/4 with a documented event, public before the 4:00 PM ET
close, a completed challenge, and no decisive contradiction.

**Verdicts:** `leading`; `needs_review` when a leading explanation's credible sources conflict;
`shared_move` when nothing leads but prices show the move was mostly sector- or market-wide;
`unresolved` otherwise; `no_anomaly` for normal-range days.

## 3. One investigation, end to end

```mermaid
sequenceDiagram
    actor U as User
    participant UI as React UI
    participant API as FastAPI
    participant RM as RunManager
    participant LG as LangGraph
    participant EXT as Data sources + OpenAI
    participant DB as app.sqlite

    U->>UI: NVDA, 2026-11-20
    UI->>API: POST /api/cases
    API->>DB: create case row
    API->>RM: start background run
    API-->>UI: case_id
    UI->>API: GET /api/cases/{id}/events (SSE)
    RM->>LG: astream(updates + custom)
    LG->>EXT: resolve_case: EDGAR lookup
    opt name ambiguous or not in EDGAR
        LG-->>RM: interrupt(candidates)
        RM-->>UI: SSE interrupt, then done (waiting for input)
        U->>UI: picks a ticker
        UI->>API: POST /api/cases/{id}/resume
        API->>RM: new run with Command(resume=ticker)
        RM->>LG: astream(Command), resolve_case re-runs
    end
    loop each remaining node
        LG->>EXT: market data, filings, news, LLM calls
        LG-->>RM: progress events + state update
        RM-->>UI: SSE progress / snapshot
    end
    LG-->>RM: report
    RM->>DB: index case (verdict, z, headline)
    RM-->>UI: SSE done
    U->>UI: follow-up question
    UI->>API: POST /api/cases/{id}/ask
    API->>LG: read checkpointed case state
    API->>EXT: coordinator answers from the case file
    API->>DB: save question + answer
    API-->>UI: answer with cited evidence IDs
```

## 4. What the LLM decides vs. what code enforces

| OpenAI roles | Deterministic Python |
|---|---|
| Coordinator: pick peers, write the case narrative, normal-day brief and follow-up answers | Returns, z-scores, beta, abnormal return, move scope, intraday path |
| Analyst: propose and refine hypotheses | Evidence IDs, timestamps, before/during/after labels |
| Researcher: choose tools and queries, summarize findings | Timing rule: after-close items can't be the trigger |
| Critic: judge event documented, price fit, independent support; plan challenges | Drops nonexistent cited IDs; independence needs ≥2 source kinds; price-fit guards |
| | Pruning, beam, budgets, routing, final verdict |

## 5. Researcher tools

| Tool | Source | Returns |
|---|---|---|
| `search_news` | Google News RSS, NewsAPI, Yahoo | Articles around the event date, labeled before/during/after |
| `list_sec_filings` | SEC EDGAR | 8-K, 10-Q, 10-K and others near the event, with acceptance times |
| `search_sec_filing` | SEC EDGAR + MiniLM | Top 5 passages from one filing and its press-release exhibit |
| `get_daily_returns` | Alpha Vantage / Yahoo | Same-day returns for up to 6 peers, ETFs or indices |
| `get_macro_context` | FRED | Treasury yield and VIX moves, macro releases in the 3 days before |
| `get_earnings_and_analyst_actions` | Yahoo Finance | Latest EPS vs consensus, recent upgrades and downgrades |
