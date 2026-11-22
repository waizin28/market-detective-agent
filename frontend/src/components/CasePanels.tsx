import { useMemo, useState } from "react";
import {
  AlertTriangle,
  BarChart3,
  CheckCircle2,
  CircleDashed,
  CircleHelp,
  Clock,
  FileText,
  Globe2,
  Landmark,
  Newspaper,
  Scale,
  Send,
  ShieldAlert,
  Sparkles,
  XCircle,
} from "lucide-react";
import type { Branch, CaseView, Evidence, Message, Report } from "../lib/types";
import { CATEGORY_LABEL, SCOPE_LABEL, compact, dateTime, money, num, pct, tone } from "../lib/format";
import { CitedText } from "./Cite";

type OnCite = (id: string) => void;

// ---------------------------------------------------------------- verdict
/** Deterministic, number-only read of the move: how much was shared with the sector/market. */
function moveBreakdown(c: CaseView): string | null {
  const a = c.anomaly;
  if (!a) return null;
  const dir = a.ret < 0 ? "fell" : "rose";
  const sector = a.benchmarks.find((b) => b.role === "sector" && b.ret != null);
  const market = a.benchmarks.find((b) => b.role === "market" && b.ret != null);
  const peers = a.benchmarks.filter((b) => b.role === "peer" && b.ret != null);
  const parts = [`${a.ticker} ${dir} ${pct(Math.abs(a.ret)).replace("+", "")} on ${a.event_date}.`];
  const same = (x: number) => Math.sign(x) === Math.sign(a.ret);
  const ctx: string[] = [];
  if (sector) ctx.push(`the sector ETF ${sector.ticker} ${pct(sector.ret!)}`);
  if (peers.length) ctx.push(`peers ${peers.map((p) => `${p.ticker} ${pct(p.ret!)}`).join(", ")}`);
  if (market) ctx.push(`the S&P 500 (${market.ticker}) ${pct(market.ret!)}`);
  if (ctx.length) parts.push(`Same day: ${ctx.join("; ")}.`);
  if (sector && same(sector.ret!) && Math.abs(a.ret) > 0) {
    const share = Math.min(1, Math.abs(sector.ret!) / Math.abs(a.ret));
    parts.push(
      share >= 0.6
        ? `About ${Math.round(share * 100)}% of the move was shared with its sector, so most of it was a sector-wide move; the rest (${pct(a.excess_vs_sector ?? a.ret - sector.ret!)}) was specific to ${a.ticker}.`
        : `Only about ${Math.round(share * 100)}% of the move was matched by its sector, so most of it was specific to ${a.ticker}.`,
    );
  } else if (sector) {
    parts.push(`Its sector moved the other way, so the move was specific to ${a.ticker}.`);
  }
  return parts.join(" ");
}

export function VerdictBadge({ c }: { c: CaseView }) {
  const v = c.report?.verdict?.status;
  if (c.status === "error") return <span className="pill bad"><XCircle size={13} /> Failed</span>;
  if (c.status === "waiting_for_input") return <span className="pill warn"><CircleHelp size={13} /> Needs your input</span>;
  if (!v) return <span className="pill blue"><CircleDashed size={13} className="spin" /> Investigating</span>;
  if (v === "leading") return <span className="pill good"><CheckCircle2 size={13} /> Leading explanation</span>;
  if (v === "needs_review") return <span className="pill warn"><ShieldAlert size={13} /> Needs analyst review</span>;
  if (v === "no_anomaly") return <span className="pill gray"><CheckCircle2 size={13} /> No unusual move</span>;
  return <span className="pill warn"><CircleHelp size={13} /> Cause unresolved</span>;
}

export function VerdictCard({ c, onCite }: { c: CaseView; onCite: OnCite }) {
  const r = c.report;
  const known = useMemo(() => new Set(c.evidence.map((e) => e.id)), [c.evidence]);
  const leading = r?.explanations.filter((e) => r.verdict.leading_ids.includes(e.branch_id)) ?? [];
  const withheld = r?.explanations.filter((e) => r.verdict.withheld_ids?.includes(e.branch_id)) ?? [];
  const top = leading[0] ?? withheld[0];
  // Unresolved: still show the best-ranked candidate, clearly labeled as unconfirmed.
  const best = top ? undefined : r?.explanations.find((e) => e.status !== "ruled_out" && (e.score?.total ?? 0) > 0);
  const shown = top ?? best;
  const support = shown?.score?.supporting_ids ?? [];
  const contra = shown?.score?.contradicting_ids ?? [];
  const breakdown = moveBreakdown(c);
  const evById = useMemo(() => new Map(c.evidence.map((e) => [e.id, e])), [c.evidence]);

  return (
    <div className="card">
      <div className="card-h"><h3>Case verdict</h3></div>
      <VerdictBadge c={c} />
      {!r && <p className="verdict-text sub">The verdict appears after the final challenge round.</p>}
      {r && r.verdict.status !== "no_anomaly" && (
        <>
          <div className="section-label"><Sparkles size={13} /> Why it {c.anomaly && c.anomaly.ret < 0 ? "went down" : "went up"}</div>
          <p className="verdict-text">
            {r.why_it_moved
              ? <CitedText text={r.why_it_moved} onCite={onCite} known={known} />
              : <CitedText text={r.summary} onCite={onCite} known={known} />}
          </p>
          {breakdown && <p className="sub" style={{ marginTop: -4 }}><BarChart3 size={12} /> {breakdown}</p>}
        </>
      )}
      {r && top && (
        <>
          <div className="section-label good"><CheckCircle2 size={13} /> {leading.length ? "Leading explanation" : "Withheld for review"}</div>
          <p className="verdict-text">
            <strong>{top.title}.</strong> <CitedText text={top.narrative || top.statement} onCite={onCite} known={known} />
          </p>
          {leading.length > 1 && <p className="sub">{leading.length} explanations are equally supported and share rank 1.</p>}
        </>
      )}
      {r && !top && best && r.verdict.status !== "no_anomaly" && (
        <>
          <div className="section-label warn"><CircleHelp size={13} /> Most likely, not confirmed · {best.score?.total ?? 0}/4</div>
          <p className="verdict-text">
            <strong>{best.title}.</strong> <CitedText text={best.narrative || best.statement} onCite={onCite} known={known} />
          </p>
          {best.blockers.length > 0 && (
            <p className="sub">Not confirmed because: {best.blockers.map((x) => x.replace(/^Not leading: /, "")).join("; ")}.</p>
          )}
        </>
      )}
      {r && (top || best) && support.length > 0 && (
        <>
          <div className="section-label good"><CheckCircle2 size={13} /> Supporting evidence</div>
          <ul className="list">
            {support.slice(0, 4).map((id) => (
              <li key={id}><CheckCircle2 size={15} color="var(--good)" />
                <span><button className="cite" onClick={() => onCite(id)}>{id}</button> {evById.get(id)?.title}</span></li>
            ))}
          </ul>
        </>
      )}
      {r && (top || best) && contra.length > 0 && (
        <>
          <div className="section-label bad"><AlertTriangle size={13} /> Contradicting evidence</div>
          <ul className="list">
            {contra.slice(0, 3).map((id) => (
              <li key={id}><AlertTriangle size={15} color="var(--bad)" />
                <span><button className="cite" onClick={() => onCite(id)}>{id}</button> {evById.get(id)?.title}</span></li>
            ))}
          </ul>
        </>
      )}
      {r && r.verdict.status === "no_anomaly" && <p className="verdict-text"><CitedText text={r.summary} onCite={onCite} /></p>}
      {r && r.uncertainties.length > 0 && (
        <>
          <div className="section-label warn"><AlertTriangle size={13} /> Remaining uncertainty</div>
          <ul className="list">
            {r.uncertainties.slice(0, 5).map((u, i) => (
              <li key={i}><AlertTriangle size={15} color="var(--warn)" /><span><CitedText text={u} onCite={onCite} known={known} /></span></li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- hypothesis tree
const RUBRIC: [keyof NonNullable<Branch["score"]>, string][] = [
  ["event", "Event"],
  ["timing", "Timing"],
  ["price_fit", "Price fit"],
  ["independent", "Indep."],
];

const STATUS_PILL: Record<string, [string, string]> = {
  active: ["blue", "Active"],
  finalist: ["blue", "Finalist"],
  leading: ["good", "Leading"],
  pruned: ["gray", "Pruned"],
  ruled_out: ["bad", "Ruled out"],
  not_established: ["warn", "Not established"],
};

export function BranchCard({ b, onCite, expanded }: { b: Branch; onCite: OnCite; expanded?: boolean }) {
  const s = b.score;
  const [cls, label] = STATUS_PILL[b.status] ?? ["gray", b.status];
  return (
    <div className={`branch ${b.status}`}>
      <div className="branch-h">
        <span className="id">{b.id}</span>
        <span className="title">{b.title}</span>
        <span className="score">{s ? `${s.total}/4` : "–/4"}</span>
      </div>
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
        <span className={`pill ${cls}`}>{label}</span>
        <span className="pill gray">{CATEGORY_LABEL[b.category] ?? b.category}</span>
        {b.challenge_completed && <span className="pill gray"><Scale size={12} /> Challenged</span>}
        {s?.decisive_contradiction && <span className="pill bad"><XCircle size={12} /> Contradicted</span>}
      </div>
      <div className="rubric" aria-label="Rubric">
        {RUBRIC.map(([k, name]) => (
          <div className="cell" key={k}>
            <div className={`bar ${s ? (s[k] ? "yes" : "no") : ""}`} />
            <span>{s ? (s[k] ? "✓ " : "✗ ") : ""}{name}</span>
          </div>
        ))}
      </div>
      {expanded && (
        <>
          <div className="stmt">{b.statement}</div>
          <div className="stmt"><strong>Predicts:</strong> {b.prediction}</div>
          <div className="stmt"><strong>Would weaken it:</strong> {b.would_weaken}</div>
          {b.critic_rationale && <div className="stmt"><strong>Critic:</strong> <CitedText text={b.critic_rationale} onCite={onCite} /></div>}
          {b.score_history.length > 0 && (
            <div className="sub">Score by round: {b.score_history.map((h) => `R${h.round} ${h.total}/4`).join(" → ")}</div>
          )}
        </>
      )}
      {(b.blockers?.length || b.notes.length) ? (
        <ul className="notes">
          {(b.blockers ?? []).map((x, i) => <li key={`b${i}`}>Not leading: {x}</li>)}
          {(expanded ? b.notes : b.notes.slice(-2)).map((n, i) => <li key={i}>{n}</li>)}
        </ul>
      ) : null}
    </div>
  );
}

export function HypothesisTree({ c, onCite }: { c: CaseView; onCite: OnCite }) {
  return (
    <div className="card">
      <div className="card-h"><h3>Hypothesis tree</h3><div className="spacer" /><span className="sub">beam width 2</span></div>
      <RoundProgress c={c} />
      {c.branches.length === 0 ? (
        <div className="empty">Hypotheses appear after the timeline is built.</div>
      ) : (
        <div className="hyp-list">
          {c.branches.map((b) => <BranchCard key={b.id} b={b} onCite={onCite} />)}
        </div>
      )}
    </div>
  );
}

export function RoundProgress({ c }: { c: CaseView }) {
  const done = !!c.report;
  const rounds = c.report?.rounds_run ?? c.round;
  const allPruned = done && c.branches.length > 0 && c.branches.every((b) => b.status === "pruned" || b.status === "ruled_out");
  return (
    <div className="progress">
      <span>
        {done ? `Finished after round ${Math.max(rounds, 0)}/3` : `Round ${Math.max(c.round, 0)}/3`}
        {allPruned && <span className="sub"> · every explanation was pruned</span>}
      </span>
      <div className="meter"><div style={{ width: `${(c.checks_used / c.max_checks) * 100}%` }} /></div>
      <span>{c.checks_used}/{c.max_checks} evidence checks</span>
    </div>
  );
}

// ---------------------------------------------------------------- event-day comparison
export function EventComparison({ c }: { c: CaseView }) {
  const a = c.anomaly;
  if (!a) return <div className="empty">Waiting for market data…</div>;
  const color = (role: string) => (role === "market" ? "var(--s-market)" : role === "sector" ? "var(--s-sector)" : "var(--s-peer)");
  return (
    <div>
      <div className="card-h"><h3>Event-day returns</h3><div className="spacer" /><span className="sub">{a.event_date}</span></div>
      <table className="kv">
        <tbody>
          <tr>
            <td><span className="swatch" style={{ background: "var(--s-stock)" }} />{a.ticker}</td>
            <td className={tone(a.ret)}>{pct(a.ret)}</td>
          </tr>
          {a.benchmarks.map((b) => (
            <tr key={b.ticker}>
              <td><span className="swatch" style={{ background: color(b.role) }} />{b.ticker} <span className="sub">{b.role}</span></td>
              <td className={tone(b.ret)}>{b.available ? pct(b.ret) : "n/a"}</td>
            </tr>
          ))}
          <tr><td>Excess vs market</td><td className={tone(a.excess_vs_market)}>{pct(a.excess_vs_market)}</td></tr>
          <tr><td>Excess vs sector</td><td className={tone(a.excess_vs_sector)}>{pct(a.excess_vs_sector)}</td></tr>
          <tr><td>Market-model abnormal return</td><td className={tone(a.abnormal_ret)}>{pct(a.abnormal_ret)}</td></tr>
          <tr><td>Beta (60d)</td><td>{num(a.beta)}</td></tr>
        </tbody>
      </table>
    </div>
  );
}

// ---------------------------------------------------------------- metrics row
export function MetricsRow({ c }: { c: CaseView }) {
  const a = c.anomaly;
  const items: [string, string, string?, string?][] = a
    ? [
        ["Daily move", pct(a.ret), tone(a.ret), `${a.prev_date} → ${a.event_date}`],
        ["z-score", `${a.z_score >= 0 ? "+" : ""}${a.z_score.toFixed(2)}σ`, a.flagged ? "neg" : "", a.flagged ? "flagged (|z| ≥ 2)" : "within range"],
        ["60-day volatility", `${(a.std_ret * 100).toFixed(2)}%`, "", "daily std. dev."],
        ["Volume", a.volume_ratio ? `${a.volume_ratio.toFixed(1)}×` : "—", "", "vs 20-day avg"],
        ["Market cap", compact(c.profile?.market_cap), "", c.profile?.industry ?? ""],
        ["52-week range", c.profile?.week52_low ? `${money(c.profile.week52_low)}–${c.profile.week52_high?.toFixed(0)}` : "—", "", ""],
      ]
    : [];
  if (!a) return null;
  return (
    <div className="metrics">
      {items.map(([label, value, cls, hint]) => (
        <div className="metric" key={label}>
          <div className="label">{label}</div>
          <div className={`value ${cls ?? ""}`}>{value}</div>
          {hint && <div className="hint">{hint}</div>}
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------- AI analysis
export function AnalysisCard({ c, onCite }: { c: CaseView; onCite: OnCite }) {
  const r: Report | null | undefined = c.report;
  const known = useMemo(() => new Set(c.evidence.map((e) => e.id)), [c.evidence]);
  const last = c.events[c.events.length - 1];
  return (
    <div className="card analysis">
      <div className="card-h">
        <h3>{r ? r.headline : "Investigation in progress"}</h3>
        <span className="pill blue"><Sparkles size={12} /> Agent analysis</span>
      </div>
      {r ? (
        <p><CitedText text={r.summary} onCite={onCite} known={known} /></p>
      ) : (
        <p className="sub">{last ? `${last.role}: ${last.message}` : "Starting…"}</p>
      )}
      <div className="tags">
        <VerdictBadge c={c} />
        {c.anomaly && <span className="pill blue"><Globe2 size={12} /> {SCOPE_LABEL[c.anomaly.scope]}</span>}
        {c.anomaly?.data_issues.length ? <span className="pill warn"><AlertTriangle size={12} /> Data issues</span> : null}
        {c.evidence.some((e) => e.flags.includes("possible_injection")) && (
          <span className="pill bad"><ShieldAlert size={12} /> Suspicious content flagged</span>
        )}
      </div>
      {r && <div className="disclaimer">{r.disclaimer}</div>}
    </div>
  );
}

// ---------------------------------------------------------------- timeline / evidence
const KIND_ICON: Record<string, JSX.Element> = {
  sec_filing: <Landmark size={17} />,
  news: <Newspaper size={17} />,
  market_data: <BarChart3 size={17} />,
  macro_data: <Globe2 size={17} />,
  computed: <Sparkles size={17} />,
};

const TIMING_PILL: Record<string, [string, string]> = {
  before: ["gray", "Before move"],
  during: ["blue", "During move"],
  after: ["warn", "After close"],
  unknown: ["gray", "Time unknown"],
};

const TIMING_RANK: Record<string, number> = { during: 0, before: 1, unknown: 2, after: 3 };

export function TimingPill({ t }: { t: string }) {
  const [cls, label] = TIMING_PILL[t] ?? ["gray", t];
  return <span className={`pill ${cls}`}><Clock size={11} /> {label}</span>;
}

export function Timeline({
  c, onOpen, limit, highlight,
}: { c: CaseView; onOpen: (e: Evidence) => void; limit?: number; highlight?: string | null }) {
  const [filter, setFilter] = useState<string>("all");
  const kinds = ["all", "sec_filing", "news", "market_data", "macro_data"];
  const items = c.evidence
    .filter((e) => filter === "all" || e.kind === filter)
    .filter((e) => !(e.kind === "sec_filing" && e.meta?.parent)) // passages live under their filing
    .slice()
    .reverse()
    // In the overview, lead with what was public by the close.
    .sort((x, y) => (limit ? TIMING_RANK[x.timing] - TIMING_RANK[y.timing] : 0));
  const shown = limit ? items.slice(0, limit) : items;
  return (
    <div>
      {!limit && (
        <div className="filters" style={{ marginBottom: 8 }}>
          {kinds.map((k) => (
            <button key={k} className={filter === k ? "active" : ""} onClick={() => setFilter(k)}>
              {k === "all" ? "All" : k.replace("_", " ")} ({k === "all" ? c.evidence.length : c.evidence.filter((e) => e.kind === k).length})
            </button>
          ))}
        </div>
      )}
      {shown.length === 0 ? (
        <div className="empty">No evidence yet.</div>
      ) : (
        <div className="timeline">
          {shown.map((e) => (
            <div key={e.id} className={`ev ${e.timing === "after" ? "after" : ""} ${highlight === e.id ? "highlight" : ""}`} id={`ev-${e.id}`}>
              <div className="ev-icon">{KIND_ICON[e.kind] ?? <FileText size={17} />}</div>
              <div style={{ minWidth: 0 }}>
                <div className="ev-title" onClick={() => onOpen(e)}>{e.title}</div>
                <div className="ev-meta">
                  <span>{e.source}</span>·<span>{dateTime(e.published_at)}</span>
                  {e.flags.includes("possible_injection") && <span className="pill bad"><ShieldAlert size={11} /> Suspicious</span>}
                </div>
              </div>
              <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: 4 }}>
                <TimingPill t={e.timing} />
                <button className="cite" onClick={() => onOpen(e)}>{e.id}</button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- agent trace
export function AgentTrace({ c }: { c: CaseView }) {
  return (
    <div className="trace">
      {c.events.length === 0 && <div className="empty">No activity yet.</div>}
      {c.events.map((e, i) => (
        <div className="trace-row" key={i}>
          <span className="when">{new Date(e.at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}</span>
          <span className={`role ${e.role}`}>{e.role}</span>
          <span>{e.message}</span>
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------- findings
export function Findings({ c, onCite }: { c: CaseView; onCite: OnCite }) {
  if (!c.findings.length) return <div className="empty">Evidence checks appear here as the researcher completes them.</div>;
  const known = new Set(c.evidence.map((e) => e.id));
  const outcomeCls: Record<string, string> = { supports: "good", contradicts: "bad", mixed: "warn", insufficient: "gray" };
  return (
    <div className="table-wrap">
      <table className="data">
        <thead><tr><th>Round</th><th>Branch</th><th>Check</th><th>Outcome</th></tr></thead>
        <tbody>
          {c.findings.map((f, i) => (
            <tr key={i}>
              <td>R{f.round} <span className="sub">{f.purpose}</span></td>
              <td className="mono">{f.branch_id}</td>
              <td>
                <div>{f.question}</div>
                <div className="sub" style={{ marginTop: 4 }}><CitedText text={f.summary} onCite={onCite} known={known} /></div>
              </td>
              <td><span className={`pill ${outcomeCls[f.outcome] ?? "gray"}`}>{f.status === "failed" ? "failed" : f.outcome}</span></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ---------------------------------------------------------------- follow up
const SUGGESTIONS = [
  "What would change this conclusion?",
  "How did peers react the same day?",
  "Which evidence was published after the move?",
  "What should I monitor next?",
];

export function FollowUp({
  c, onAsk, busy, onCite,
}: { c: CaseView; onAsk: (q: string) => void; busy: boolean; onCite: OnCite }) {
  const [q, setQ] = useState("");
  const ready = !!c.report && c.report.verdict.status !== "no_anomaly";
  const known = new Set(c.evidence.map((e) => e.id));
  const submit = (text: string) => {
    if (!text.trim() || busy) return;
    onAsk(text.trim());
    setQ("");
  };
  return (
    <div className="card">
      <div className="card-h"><h3>Ask a follow-up question</h3></div>
      {(c.messages ?? []).length > 0 && (
        <div className="msgs">
          {(c.messages as Message[]).map((m, i) => (
            <div key={i} className={`msg ${m.role}`}>
              {m.role === "assistant" ? <CitedText text={m.content} onCite={onCite} known={known} /> : m.content}
            </div>
          ))}
        </div>
      )}
      <form className="ask-box" onSubmit={(e) => { e.preventDefault(); submit(q); }}>
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder={ready ? "e.g. Was the guidance cut public before the drop?" : "Available once the case file is ready"}
          disabled={!ready || busy}
        />
        <button className="btn primary sm" disabled={!ready || busy || !q.trim()} aria-label="Send">
          {busy ? <CircleDashed size={15} className="spin" /> : <Send size={15} />}
        </button>
      </form>
      {ready && (
        <div className="chips" style={{ marginTop: 10 }}>
          {SUGGESTIONS.map((s) => <button key={s} className="chip" onClick={() => submit(s)} disabled={busy}>{s}</button>)}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- next steps
export function NextSteps({ c, onCite }: { c: CaseView; onCite: OnCite }) {
  const steps = c.report?.next_steps ?? [];
  const gaps = c.gaps ?? [];
  if (!steps.length && !gaps.length) return null;
  return (
    <div className="card">
      {steps.length > 0 && (
        <>
          <div className="card-h"><Sparkles size={16} color="#8db8ff" /><h3>Next steps</h3></div>
          <ul className="list">
            {steps.map((s, i) => <li key={i}><CheckCircle2 size={15} color="#8db8ff" /><span><CitedText text={s} onCite={onCite} /></span></li>)}
          </ul>
        </>
      )}
      {gaps.length > 0 && (
        <>
          <div className="section-label warn" style={{ marginTop: steps.length ? 16 : 0 }}><AlertTriangle size={13} /> Evidence gaps</div>
          <ul className="list">
            {gaps.slice(0, 8).map((g, i) => <li key={i}><CircleHelp size={15} color="var(--warn)" /><span className="sub">{g}</span></li>)}
          </ul>
        </>
      )}
    </div>
  );
}
