import { useEffect, useMemo, useState } from "react";
import { api } from "../lib/api";
import type { CaseView, Evidence } from "../lib/types";
import { CATEGORY_LABEL, SCOPE_LABEL, money, pct } from "../lib/format";
import { etDate, etTime, leadUpRows } from "../components/CasePanels";

const VERDICT: Record<string, string> = {
  leading: "Leading explanation found",
  unresolved: "Unresolved: no explanation met the bar",
  needs_review: "Needs review",
  no_anomaly: "Normal trading day",
};
const ROLE: Record<string, string> = { market: "Market", sector: "Sector", peer: "Peer" };

/** Printable case file: opens in its own tab and offers the print dialog (Save as PDF). */
export function CaseFile({ caseId }: { caseId: string }) {
  const [c, setC] = useState<CaseView | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    document.body.classList.add("print-mode");
    return () => document.body.classList.remove("print-mode");
  }, []);
  useEffect(() => {
    api.getCase(caseId).then(setC).catch((e) => setErr((e as Error).message));
  }, [caseId]);
  useEffect(() => {
    if (!c) return;
    document.title = `Market Detective - ${c.ticker ?? c.query} ${c.anomaly?.event_date ?? ""}`;
    const t = window.setTimeout(() => window.print(), 600);
    return () => window.clearTimeout(t);
  }, [c]);

  const byId = useMemo(() => new Map((c?.evidence ?? []).map((e) => [e.id, e])), [c]);
  if (!c) return <div className="cf"><p>{err ? `Couldn't load case: ${err}` : "Preparing case file…"}</p></div>;

  const a = c.anomaly, r = c.report;
  const rows = leadUpRows(c, 12);
  const cited = new Set<string>(rows.map((x) => x.e.id));
  r?.explanations.forEach((x) => x.score?.supporting_ids.forEach((i) => cited.add(i)));
  const sources = [...cited].map((i) => byId.get(i)).filter((e): e is Evidence => !!e)
    .sort((x, y) => (x.published_at ?? "").localeCompare(y.published_at ?? ""));
  const intr = c.intraday?.summary;

  return (
    <div className="cf">
      <div className="cf-actions no-print">
        <button className="btn" onClick={() => window.print()}>Print / Save as PDF</button>
        <span>In the print dialog, choose “Save as PDF”.</span>
      </div>

      <header className="cf-head">
        <div>
          <p className="cf-eyebrow">Market Detective · Case file</p>
          <h1>{c.company ?? c.ticker} ({c.ticker})</h1>
          {a && <p className="cf-sub">Close on {a.event_date}: {money(a.close)} ({pct(a.ret)}) · z-score {a.z_score.toFixed(2)} · {SCOPE_LABEL[a.scope] ?? a.scope}</p>}
        </div>
        {r && <div className={`cf-verdict v-${r.verdict.status}`}>{VERDICT[r.verdict.status] ?? r.verdict.status}</div>}
      </header>

      {r && (
        <section>
          <h2>{r.headline}</h2>
          {r.why_it_moved && <p className="cf-lede">{r.why_it_moved}</p>}
          <p>{r.summary}</p>
        </section>
      )}

      {a && (
        <section>
          <h3>The move in context</h3>
          <table className="cf-table">
            <thead><tr><th>Ticker</th><th>Role</th><th>Return on {a.event_date}</th></tr></thead>
            <tbody>
              <tr><td><b>{c.ticker}</b></td><td>Stock</td><td>{pct(a.ret)}</td></tr>
              {a.benchmarks.filter((b) => b.ret != null).map((b) => (
                <tr key={b.ticker}><td>{b.ticker}</td><td>{ROLE[b.role] ?? b.role}</td><td>{pct(b.ret)}</td></tr>
              ))}
            </tbody>
          </table>
          {intr && (
            <p className="cf-note">
              Intraday: {pct(intr.open_pct, 1)} after 5 minutes, low {pct(intr.low_pct, 1)} at {intr.low_at} ET, close {pct(intr.close_pct, 1)}.{" "}
              {Object.entries(intr.share_by).filter(([, v]) => v != null).map(([k, v]) => `${Math.round((v as number) * 100)}% of the move by ${k}`).join(", ")}.
            </p>
          )}
        </section>
      )}

      {rows.length > 0 && (
        <section>
          <h3>What led up to the move</h3>
          <ol className="cf-timeline">
            {rows.map(({ e, headline, brief }) => (
              <li key={e.id}>
                <div className="cf-when">{etDate(e.published_at)} {!e.date_only && etTime(e.published_at)} · {e.source} · [{e.id}]</div>
                <div className="cf-ev-title">{headline || e.title}</div>
                {brief && <div className="cf-ev-brief">{brief}</div>}
              </li>
            ))}
          </ol>
        </section>
      )}

      {r && r.explanations.length > 0 && (
        <section>
          <h3>Explanations tested</h3>
          <table className="cf-table">
            <thead><tr><th>Explanation</th><th>Type</th><th>Score</th><th>Status</th></tr></thead>
            <tbody>
              {r.explanations.map((x) => (
                <tr key={x.branch_id}>
                  <td><b>{x.title}</b><div className="cf-small">{x.narrative || x.statement}</div></td>
                  <td>{CATEGORY_LABEL[x.category] ?? x.category}</td>
                  <td>{x.score ? `${x.score.total}/4` : "—"}</td>
                  <td>{x.status.replace("_", " ")}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="cf-small">Rubric: event documented · public before the 4:00 PM ET close · prices fit · independent support. A 3/4 score is not a 75% probability.</p>
        </section>
      )}

      {r && r.uncertainties.length > 0 && (
        <section>
          <h3>What remains uncertain</h3>
          <ul>{r.uncertainties.map((u, i) => <li key={i}>{u}</li>)}</ul>
        </section>
      )}

      {(c.upcoming ?? []).length > 0 && (
        <section>
          <h3>What to watch next</h3>
          <ul>{c.upcoming!.map((u) => <li key={u.date + u.title}><b>{u.date}</b> · {u.title}. {u.detail}</li>)}</ul>
        </section>
      )}

      {sources.length > 0 && (
        <section>
          <h3>Sources</h3>
          <ol className="cf-sources">
            {sources.map((e) => (
              <li key={e.id}>[{e.id}] {e.title}. {e.source}, {etDate(e.published_at)} {!e.date_only && etTime(e.published_at)}.{" "}
                {e.url && <span className="cf-url">{e.url}</span>}</li>
            ))}
          </ol>
        </section>
      )}

      <footer className="cf-foot">
        {r?.disclaimer ?? "Research, not investment advice."} Generated {new Date(r?.generated_at ?? Date.now()).toLocaleString()} · case {c.case_id}.
      </footer>
    </div>
  );
}
