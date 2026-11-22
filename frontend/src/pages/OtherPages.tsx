import { useEffect, useState } from "react";
import { CheckCircle2, CircleDashed, Plus, RefreshCw, Search, Trash2, XCircle } from "lucide-react";
import { api } from "../lib/api";
import type { AppConfig, CaseSummary, ScanRow } from "../lib/types";
import { ago, money, pct, tone } from "../lib/format";
import { Sparkline } from "../components/PriceChart";

// ---------------------------------------------------------------- watchlist
export function WatchlistPage({ onInvestigate }: { onInvestigate: (t: string, date?: string) => void }) {
  const [rows, setRows] = useState<ScanRow[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [add, setAdd] = useState("");
  const [err, setErr] = useState<string | null>(null);

  const load = async () => {
    setBusy(true);
    setErr(null);
    try { setRows(await api.scan()); } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  };
  useEffect(() => { load(); }, []);

  const addTicker = async () => {
    const t = add.trim().toUpperCase();
    if (!t) return;
    await api.addWatch(t);
    setAdd("");
    load();
  };
  const remove = async (t: string) => { await api.removeWatch(t); setRows((r) => r?.filter((x) => x.ticker !== t) ?? null); };

  return (
    <div className="card">
      <div className="card-h">
        <h3>Unusual-move scanner</h3>
        <span className="sub">largest move in the last 20 trading days, z-scored against the prior 60</span>
        <div className="spacer" />
        <button className="btn sm" onClick={load} disabled={busy}><RefreshCw size={14} className={busy ? "spin" : ""} /> Rescan</button>
      </div>
      <form className="search-row" style={{ marginBottom: 14 }} onSubmit={(e) => { e.preventDefault(); addTicker(); }}>
        <div className="search-box" style={{ maxWidth: 260 }}>
          <Plus size={16} color="#7184a3" />
          <input value={add} onChange={(e) => setAdd(e.target.value)} placeholder="Add ticker" maxLength={12} />
        </div>
        <button className="btn" disabled={!add.trim()}>Add</button>
      </form>
      {err && <div className="banner bad">{err}</div>}
      {!rows ? <div className="empty"><CircleDashed className="spin" size={18} /> Scanning…</div> : (
        <div className="table-wrap">
          <table className="data">
            <thead>
              <tr>
                <th>Ticker</th><th>40-day trend</th><th className="r">Last close</th><th className="r">Last day</th>
                <th>Largest recent move</th><th className="r">z-score</th><th>Status</th><th />
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.ticker}>
                  <td><strong>{r.ticker}</strong></td>
                  {r.error ? (
                    <td colSpan={6} className="sub">{r.error}</td>
                  ) : (
                    <>
                      <td><Sparkline values={r.spark ?? []} /></td>
                      <td className="r">{money(r.last_close)}</td>
                      <td className={`r ${tone(r.last_ret)}`}>{pct(r.last_ret)}</td>
                      <td><span className={tone(r.event_ret)}>{pct(r.event_ret)}</span> <span className="sub">on {r.event_date}</span></td>
                      <td className="r mono">{r.z_score !== undefined ? `${r.z_score >= 0 ? "+" : ""}${r.z_score.toFixed(2)}σ` : "—"}</td>
                      <td>
                        {r.flagged ? <span className="pill warn">⚑ Unusual</span> : <span className="pill gray">Normal</span>}{" "}
                        {r.flagged && r.vs_market === "with_market" && <span className="pill gray">with market</span>}
                      </td>
                    </>
                  )}
                  <td style={{ whiteSpace: "nowrap", textAlign: "right" }}>
                    {!r.error && (
                      <button className="btn sm primary" onClick={() => onInvestigate(r.ticker, r.event_date)}>
                        <Search size={13} /> Investigate
                      </button>
                    )}{" "}
                    <button className="icon-btn" onClick={() => remove(r.ticker)} aria-label={`Remove ${r.ticker}`}><Trash2 size={15} /></button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- history
const VERDICT_PILL: Record<string, [string, string]> = {
  leading: ["good", "Cause identified"],
  unresolved: ["warn", "Cause not proven"],
  shared_move: ["blue", "Sector/market move"],
  needs_review: ["warn", "Needs review"],
  no_anomaly: ["gray", "No unusual move"],
};

export function HistoryPage({ onOpen }: { onOpen: (id: string) => void }) {
  const [rows, setRows] = useState<CaseSummary[] | null>(null);
  useEffect(() => { api.listCases().then(setRows).catch(() => setRows([])); }, []);
  if (!rows) return <div className="card empty">Loading…</div>;
  return (
    <div className="card">
      <div className="card-h"><h3>Research history</h3><span className="sub">{rows.length} cases</span></div>
      {rows.length === 0 ? <div className="empty">No investigations yet.</div> : (
        <div className="table-wrap">
          <table className="data">
            <thead><tr><th>Case</th><th>Move</th><th>Verdict</th><th>Started</th></tr></thead>
            <tbody>
              {rows.map((r) => {
                const [cls, label] = r.verdict ? VERDICT_PILL[r.verdict] ?? ["gray", r.verdict] :
                  r.status === "error" ? ["bad", "Failed"] : ["blue", r.status.replace(/_/g, " ")];
                return (
                  <tr key={r.id} style={{ cursor: "pointer" }} onClick={() => onOpen(r.id)}>
                    <td>
                      <strong>{r.ticker || r.query}</strong> <span className="sub">{r.company}</span>
                      <div className="sub">{r.headline || r.error || ""}</div>
                    </td>
                    <td>{r.ret != null ? <><span className={tone(r.ret)}>{pct(r.ret)}</span> <span className="sub">{r.event_date}</span></> : "—"}</td>
                    <td><span className={`pill ${cls}`}>{label}</span></td>
                    <td className="sub">{ago(r.created_at)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- settings
const KEY_INFO: Record<string, [string, string]> = {
  openai: ["OpenAI (ChatGPT models)", "Required. Powers the coordinator, analyst, researcher and critic."],
  sec_user_agent: ["SEC EDGAR user agent", "Required by SEC fair-access rules: an app name plus contact email."],
  alpha_vantage: ["Alpha Vantage", "Optional. Primary price source; falls back to Yahoo Finance."],
  newsapi: ["NewsAPI", "Optional. Without it, only recent Yahoo Finance headlines are searched."],
  fred: ["FRED", "Optional. Enables Treasury yield, VIX and macro-release checks."],
};

export function SettingsPage({ config }: { config: AppConfig | null }) {
  if (!config) return <div className="card empty">Loading…</div>;
  return (
    <div className="col" style={{ maxWidth: 820 }}>
      <div className="card">
        <div className="card-h"><h3>Data sources & keys</h3><span className="sub">set in backend/.env</span></div>
        <table className="kv">
          <tbody>
            {Object.entries(KEY_INFO).map(([k, [name, desc]]) => (
              <tr key={k}>
                <td><strong style={{ color: "var(--text)" }}>{name}</strong><div className="sub">{desc}</div></td>
                <td>{config.keys[k] ? <span className="pill good"><CheckCircle2 size={12} /> Configured</span> : <span className="pill warn"><XCircle size={12} /> Not set</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="card">
        <div className="card-h"><h3>Investigation rules</h3><span className="sub">model {config.model}</span></div>
        <table className="kv">
          <tbody>
            <tr><td>Anomaly threshold</td><td>|z| ≥ {config.limits.z_threshold} vs prior {config.limits.lookback_days} trading days</td></tr>
            <tr><td>Beam width</td><td>{config.limits.beam_width} explanations advance per round</td></tr>
            <tr><td>Rounds</td><td>{config.limits.max_rounds} (propose → refine → challenge)</td></tr>
            <tr><td>Evidence checks</td><td>at most {config.limits.max_evidence_checks} per case</td></tr>
            <tr><td>Leading explanation</td><td>≥ 3/4, documented event, public before close, survived a challenge</td></tr>
          </tbody>
        </table>
      </div>
    </div>
  );
}
