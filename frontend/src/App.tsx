import { useEffect, useState } from "react";
import { BarChart3, Clock, Eye, Home, Search, Settings, ShieldCheck, Sparkles } from "lucide-react";
import { api } from "./lib/api";
import type { AppConfig } from "./lib/types";
import { CasePage } from "./pages/CasePage";
import { HistoryPage, SettingsPage, WatchlistPage } from "./pages/OtherPages";

type Route = { page: "home" } | { page: "case"; id: string } | { page: "watchlist" } | { page: "history" } | { page: "settings" };

function parseHash(): Route {
  const h = window.location.hash.replace(/^#\/?/, "");
  const [page, id] = h.split("/");
  if (page === "case" && id) return { page: "case", id };
  if (page === "watchlist" || page === "history" || page === "settings") return { page };
  return { page: "home" };
}

const TRY = ["NVDA", "AAPL", "TSLA", "AMD", "META", "MSFT"];

function SearchBar({ onSubmit, disabled }: { onSubmit: (q: string, date: string | null, force: boolean) => Promise<void>; disabled: boolean }) {
  const [q, setQ] = useState("");
  const [date, setDate] = useState("");
  const [force, setForce] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const go = async (query: string) => {
    if (!query.trim()) return;
    setBusy(true);
    setErr(null);
    try { await onSubmit(query.trim(), date || null, force); setQ(""); }
    catch (e) { setErr((e as Error).message); }
    finally { setBusy(false); }
  };
  return (
    <div className="card search">
      <form className="search-row" onSubmit={(e) => { e.preventDefault(); go(q); }}>
        <div className="search-box">
          <Search size={17} color="#7184a3" />
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Investigate a stock: ticker or company name…" maxLength={80} />
        </div>
        <label className="date-box" title="Leave empty to investigate the largest move in the last 20 trading days">
          Event date
          <input type="date" value={date} onChange={(e) => setDate(e.target.value)} max={new Date().toISOString().slice(0, 10)} />
        </label>
        <button className="btn primary" disabled={busy || disabled || !q.trim()}>
          <Sparkles size={15} /> {busy ? "Starting…" : "Investigate"}
        </button>
      </form>
      <div className="chips">
        <span>Try:</span>
        {TRY.map((t) => <button key={t} className="chip" onClick={() => go(t)} disabled={busy || disabled}>{t}</button>)}
        <span style={{ flex: 1 }} />
        <label className="check"><input type="checkbox" checked={force} onChange={(e) => setForce(e.target.checked)} /> Investigate even if the move looks normal</label>
      </div>
      {err && <div className="banner bad">{err}</div>}
      {disabled && <div className="banner warn">The server has no ANTHROPIC_API_KEY configured. Add it to backend/.env and restart the API.</div>}
    </div>
  );
}

function Welcome() {
  const steps = [
    ["Detect", "z-score the daily move against 60 days of history and compare it with the sector ETF, peers and the S&P 500."],
    ["Hypothesize", "An analyst agent proposes up to four competing explanations and what each predicts."],
    ["Test", "A researcher agent checks each one against SEC filings, news, peer returns and macro data, in parallel."],
    ["Challenge", "A critic scores the evidence, prunes weak branches and tries to disprove the finalists."],
  ];
  return (
    <div className="hero">
      <div className="brand-mark" style={{ margin: "0 auto", width: 52, height: 52 }}><Eye size={26} color="#fff" /></div>
      <h2>Why did that stock move?</h2>
      <p>
        Market Detective investigates unusual price moves and returns an evidence-backed case file: a timeline,
        ranked explanations with supporting and contradicting evidence, and an honest account of what remains uncertain.
      </p>
      <div className="steps">
        {steps.map(([t, d], i) => (
          <div className="card" key={t}><span className="pill blue">{i + 1}</span><h4>{t}</h4><p>{d}</p></div>
        ))}
      </div>
    </div>
  );
}

export default function App() {
  const [route, setRoute] = useState<Route>(parseHash());
  const [config, setConfig] = useState<AppConfig | null>(null);

  useEffect(() => {
    const on = () => setRoute(parseHash());
    window.addEventListener("hashchange", on);
    api.config().then(setConfig).catch(() => setConfig(null));
    return () => window.removeEventListener("hashchange", on);
  }, []);

  const nav = (hash: string) => { window.location.hash = hash; };
  const start = async (query: string, date: string | null, force: boolean) => {
    const { case_id } = await api.createCase({ query, event_date: date, force });
    nav(`/case/${case_id}`);
  };
  const llmReady = config?.keys.anthropic ?? false;
  const items: [string, string, JSX.Element][] = [
    ["home", "Investigate", <Home size={17} />],
    ["watchlist", "Watchlist", <BarChart3 size={17} />],
    ["history", "Research history", <Clock size={17} />],
    ["settings", "Settings", <Settings size={17} />],
  ];
  const active = route.page === "case" ? "home" : route.page;

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <div className="brand-mark"><Eye size={20} color="#fff" /></div>
          <div><h1>Market Detective</h1><p>Unusual moves. Evidence-backed explanations.</p></div>
        </div>
        <div className="spacer" />
        <div className="agent-status">
          <span className={`dot ${config ? (llmReady ? "on" : "off") : "off"}`} />
          {config ? (llmReady ? `Agents online · ${config.model}` : "LLM key missing") : "API offline"}
        </div>
      </header>
      <div className="body">
        <nav className="sidebar">
          {items.map(([key, label, icon]) => (
            <button key={key} className={`nav-item ${active === key ? "active" : ""}`} onClick={() => nav(key === "home" ? "/" : `/${key}`)}>
              {icon} {label}
            </button>
          ))}
          <div className="side-card">
            <ShieldCheck size={20} color="#8db8ff" />
            <h4>Research, not advice</h4>
            <ul>
              <li>Read-only data tools; no trading</li>
              <li>Evidence after the close can't be a trigger</li>
              <li>Unresolved is a valid answer</li>
            </ul>
          </div>
        </nav>
        <main className="main">
          {route.page === "home" && (
            <div className="col">
              <SearchBar onSubmit={start} disabled={!!config && !llmReady} />
              <Welcome />
            </div>
          )}
          {route.page === "case" && (
            <div className="col">
              <SearchBar onSubmit={start} disabled={!!config && !llmReady} />
              <CasePage key={route.id} caseId={route.id} />
            </div>
          )}
          {route.page === "watchlist" && (
            <WatchlistPage onInvestigate={(t, d) => start(t, d ?? null, true).catch((e) => alert((e as Error).message))} />
          )}
          {route.page === "history" && <HistoryPage onOpen={(id) => nav(`/case/${id}`)} />}
          {route.page === "settings" && <SettingsPage config={config} />}
        </main>
      </div>
    </div>
  );
}
