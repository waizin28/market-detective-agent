import { useCallback, useEffect, useMemo, useState } from "react";
import { AlertTriangle, XCircle } from "lucide-react";
import { api, subscribe } from "../lib/api";
import type { CaseView, Evidence, Interrupt } from "../lib/types";
import { money, pct, tone } from "../lib/format";
import { PriceChart } from "../components/PriceChart";
import {
  AgentTrace,
  AnalysisCard,
  BranchCard,
  EventComparison,
  Findings,
  FollowUp,
  HypothesisTree,
  LeadUpTimeline,
  MetricsRow,
  NextSteps,
  RoundProgress,
  Timeline,
  VerdictCard,
} from "../components/CasePanels";
import { ClarifyModal, EvidenceDrawer } from "../components/Overlays";

type Tab = "overview" | "hypotheses" | "evidence" | "checks" | "trace";

export function CasePage({ caseId }: { caseId: string }) {
  const [c, setC] = useState<CaseView | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("overview");
  const [open, setOpen] = useState<Evidence | null>(null);
  const [asking, setAsking] = useState(false);
  const [streamKey, setStreamKey] = useState(0);

  // Initial load, then live updates over SSE.
  useEffect(() => {
    let cancelled = false;
    setErr(null);
    api.getCase(caseId).then((v) => !cancelled && setC(v)).catch((e) => !cancelled && setErr(String(e.message)));
    const unsub = subscribe(caseId, (ev) => {
      if (ev.type === "snapshot" && ev.case) {
        setC((prev) => ({ ...ev.case, messages: prev?.messages ?? ev.case.messages }));
      } else if (ev.type === "progress") {
        setC((prev) => (prev ? { ...prev, events: [...prev.events, { at: ev.at, role: ev.role, message: ev.message }].slice(-400) } : prev));
      } else if (ev.type === "interrupt") {
        setC((prev) => (prev ? { ...prev, status: "waiting_for_input", interrupt: ev.payload as Interrupt } : prev));
      } else if (ev.type === "error") {
        setErr(ev.message);
      }
    });
    return () => { cancelled = true; unsub(); };
  }, [caseId, streamKey]);

  const evById = useMemo(() => new Map((c?.evidence ?? []).map((e) => [e.id, e])), [c?.evidence]);
  const onCite = useCallback((id: string) => { const e = evById.get(id); if (e) setOpen(e); }, [evById]);

  const resume = async (ticker: string) => {
    await api.resume(caseId, ticker);
    setC((prev) => (prev ? { ...prev, interrupt: null, status: "running" } : prev));
    setStreamKey((k) => k + 1); // re-subscribe to the resumed run
  };

  const ask = async (q: string) => {
    if (!c) return;
    setAsking(true);
    const now = new Date().toISOString();
    setC({ ...c, messages: [...(c.messages ?? []), { role: "user", content: q, cited: [], created_at: now }] });
    try {
      const m = await api.ask(caseId, q);
      setC((prev) => (prev ? { ...prev, messages: [...(prev.messages ?? []), m] } : prev));
    } catch (e) {
      setC((prev) => prev ? { ...prev, messages: [...(prev.messages ?? []), { role: "assistant", content: `Sorry, that failed: ${(e as Error).message}`, cited: [], created_at: now }] } : prev);
    } finally {
      setAsking(false);
    }
  };

  if (!c) return <div className="card empty">{err ? `Couldn't load case: ${err}` : "Loading case…"}</div>;

  const a = c.anomaly;
  const tabs: [Tab, string, number?][] = [
    ["overview", "Overview"],
    ["hypotheses", "Hypotheses", c.branches.length],
    ["evidence", "Evidence", c.evidence.length],
    ["checks", "Checks", c.findings.length],
    ["trace", "Agent trace", c.events.length],
  ];

  return (
    <div className="grid">
      <div className="col">
        {(err || c.error) && (
          <div className="banner bad"><XCircle size={16} /><span>{c.error || err}</span></div>
        )}
        {a?.data_issues?.length ? (
          <div className="banner warn"><AlertTriangle size={16} /><span><strong>Data checks:</strong> {a.data_issues.join(" ")}</span></div>
        ) : null}

        <div className="card">
          <div className="case-head">
            <div className="logo">{(c.ticker || c.query).slice(0, 4).toUpperCase()}</div>
            <div className="case-title">
              <h2>{c.ticker || c.query.toUpperCase()}</h2>
              <div className="company">{c.company || "Resolving…"}</div>
              {a && (
                <>
                  <div className="price-row">
                    <span className="price">{money(a.close)}</span>
                    <span className={tone(a.ret)} style={{ fontWeight: 600 }}>
                      {a.close - a.prev_close >= 0 ? "+" : ""}{(a.close - a.prev_close).toFixed(2)} ({pct(a.ret)})
                    </span>
                  </div>
                  <div className="sub">Close on {a.event_date} · 4:00 PM ET</div>
                </>
              )}
            </div>
            <div className="tabs">
              {tabs.map(([k, label, n]) => (
                <button key={k} className={`tab ${tab === k ? "active" : ""}`} onClick={() => setTab(k)}>
                  {label}{n ? <span className="count">{n}</span> : null}
                </button>
              ))}
            </div>
          </div>
          <MetricsRow c={c} />
        </div>

        {tab === "overview" && (
          <>
            <AnalysisCard c={c} onCite={onCite} />
            <div className="split">
              <div className="card"><PriceChart c={c} /></div>
              <div className="card"><EventComparison c={c} /></div>
            </div>
            <div className="card">
              <div className="card-h">
                <h3>What led up to the move</h3>
                <span className="sub">{c.ticker} news from the week before{c.anomaly ? ` ${c.anomaly.event_date}` : ""}, public before the close</span>
                <div className="spacer" />
                <button className="btn sm" onClick={() => setTab("evidence")}>All evidence →</button>
              </div>
              <LeadUpTimeline c={c} onOpen={setOpen} limit={10} />
            </div>
            <FollowUp c={c} onAsk={ask} busy={asking} onCite={onCite} />
          </>
        )}
        {tab === "hypotheses" && (
          <div className="card">
            <div className="card-h"><h3>Competing explanations</h3></div>
            <RoundProgress c={c} />
            {c.branches.length === 0 ? <div className="empty">No hypotheses yet.</div> : (
              <div className="hyp-list">{c.branches.map((b) => <BranchCard key={b.id} b={b} onCite={onCite} expanded />)}</div>
            )}
            <p className="disclaimer">
              Rubric: event documented · public before the 4:00 PM ET close · prices fit · independent support.
              Timing and independence are verified mechanically. A 3/4 score is not a 75% probability.
            </p>
          </div>
        )}
        {tab === "evidence" && (
          <div className="card"><div className="card-h"><h3>Evidence ledger</h3></div><Timeline c={c} onOpen={setOpen} /></div>
        )}
        {tab === "checks" && (
          <div className="card"><div className="card-h"><h3>Evidence checks</h3><div className="spacer" /><RoundProgress c={c} /></div><Findings c={c} onCite={onCite} /></div>
        )}
        {tab === "trace" && (
          <div className="card">
            <div className="card-h"><h3>Agent trace</h3><div className="spacer" />{c.api_calls != null && <span className="sub">{c.api_calls} external API calls</span>}</div>
            <AgentTrace c={c} />
          </div>
        )}
      </div>

      <div className="col">
        <VerdictCard c={c} onCite={onCite} />
        <HypothesisTree c={c} onCite={onCite} />
        <NextSteps c={c} onCite={onCite} />
      </div>

      <EvidenceDrawer ev={open} onClose={() => setOpen(null)} />
      {c.interrupt && <ClarifyModal it={c.interrupt} onPick={resume} />}
    </div>
  );
}
