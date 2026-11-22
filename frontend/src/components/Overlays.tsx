import { useState } from "react";
import { ExternalLink, ShieldAlert, X } from "lucide-react";
import type { Evidence, Interrupt } from "../lib/types";
import { dateTime } from "../lib/format";
import { TimingPill } from "./CasePanels";

/** Where the information comes from. Computed items point at the public price history they derive from. */
export function sourceUrl(ev: Evidence): string | null {
  if (ev.url) return ev.url;
  if (ev.kind === "market_data" || ev.kind === "computed") {
    const t = (ev.meta?.ticker as string | undefined) ?? ev.title.split(" ")[0];
    if (/^[A-Z.\-^]{1,10}$/.test(t)) return `https://finance.yahoo.com/quote/${t}/history/`;
  }
  return null;
}

export function EvidenceDrawer({ ev, onClose }: { ev: Evidence | null; onClose: () => void }) {
  if (!ev) return null;
  const meta = Object.entries(ev.meta || {}).filter(([k]) => !["primary_document", "cik", "publisher_site"].includes(k));
  const url = sourceUrl(ev);
  const site = ev.meta?.publisher_site as string | undefined;
  return (
    <div className="overlay" onClick={onClose}>
      <aside className="drawer" onClick={(e) => e.stopPropagation()} role="dialog" aria-label="Evidence detail">
        <div className="card-h">
          <span className="cite">{ev.id}</span>
          <div className="spacer" />
          <button className="icon-btn" onClick={onClose} aria-label="Close"><X size={18} /></button>
        </div>
        <h3 style={{ margin: "0 0 8px" }}>{ev.title}</h3>
        <div className="tags" style={{ marginBottom: 12 }}>
          <TimingPill t={ev.timing} />
          <span className="pill gray">{ev.kind.replace("_", " ")}</span>
          <span className="pill gray">{ev.source}</span>
        </div>
        {ev.flags.includes("possible_injection") && (
          <div className="banner bad" style={{ marginBottom: 12 }}>
            <ShieldAlert size={16} />
            <span>This content contains instruction-like text. Agents treated it as untrusted data and did not follow it.</span>
          </div>
        )}
        {ev.timing === "after" && (
          <div className="banner warn" style={{ marginBottom: 12 }}>
            Published after the 4:00 PM ET close of the event day, so it cannot have triggered the move. It may be
            used only as retrospective commentary.
          </div>
        )}
        <table className="kv" style={{ marginBottom: 12 }}>
          <tbody>
            <tr><td>Published</td><td>{dateTime(ev.published_at)}{ev.date_only ? " (date only)" : ""}</td></tr>
            <tr><td>Retrieved</td><td>{dateTime(ev.retrieved_at)}</td></tr>
            <tr>
              <td>Source URL</td>
              <td style={{ wordBreak: "break-all", textAlign: "right" }}>
                {url ? <a href={url} target="_blank" rel="noreferrer" className="src-link">{url}</a> : <span className="sub">none (derived by Market Detective)</span>}
                {ev.kind === "computed" && url && <div className="sub">Calculated from the daily closes at this page.</div>}
              </td>
            </tr>
            {site && (
              <tr><td>Publisher</td><td style={{ textAlign: "right" }}><a href={site} target="_blank" rel="noreferrer" className="src-link">{ev.source} · {site.replace(/^https?:\/\//, "")}</a></td></tr>
            )}
            {meta.map(([k, v]) => (
              <tr key={k}><td>{k.replace("_", " ")}</td><td className="mono" style={{ fontSize: 12 }}>{Array.isArray(v) ? v.join(", ") : String(v)}</td></tr>
            ))}
          </tbody>
        </table>
        {ev.snippet && <pre>{ev.snippet}</pre>}
        {url && (
          <a href={url} target="_blank" rel="noreferrer" className="btn sm" style={{ marginTop: 10 }}>
            <ExternalLink size={14} /> Open source
          </a>
        )}
      </aside>
    </div>
  );
}

export function ClarifyModal({ it, onPick }: { it: Interrupt; onPick: (ticker: string) => Promise<void> }) {
  const [manual, setManual] = useState("");
  const [busy, setBusy] = useState(false);
  const pick = async (t: string) => {
    setBusy(true);
    try { await onPick(t); } finally { setBusy(false); }
  };
  return (
    <div className="overlay">
      <div className="modal" role="dialog" aria-label="Clarify security">
        <h3 style={{ marginTop: 0 }}>Which security?</h3>
        <p className="sub" style={{ marginTop: -4 }}>{it.question}</p>
        {it.candidates.map((c) => (
          <button key={c.ticker} className="cand" onClick={() => pick(c.ticker)} disabled={busy}>
            <strong>{c.ticker}</strong><span className="sub">{c.name}</span>
          </button>
        ))}
        <form onSubmit={(e) => { e.preventDefault(); if (manual.trim()) pick(manual.trim()); }} style={{ display: "flex", gap: 8, marginTop: 8 }}>
          <div className="search-box" style={{ minWidth: 0 }}>
            <input value={manual} onChange={(e) => setManual(e.target.value.toUpperCase())} placeholder="Or type a ticker" maxLength={12} />
          </div>
          <button className="btn primary" disabled={busy || !manual.trim()}>Continue</button>
        </form>
      </div>
    </div>
  );
}
