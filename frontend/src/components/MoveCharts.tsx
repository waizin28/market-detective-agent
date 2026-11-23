import { useMemo } from "react";
import { CalendarDays, DollarSign, Landmark, Percent } from "lucide-react";
import { CartesianGrid, Line, LineChart, ReferenceDot, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { CaseView, Evidence, Upcoming } from "../lib/types";
import { pct } from "../lib/format";
import { isQuotePage } from "./CasePanels";

export const KIND_COLOR: Record<string, string> = {
  news: "#3987e5",
  sec_filing: "#9085e9",
  analyst: "#fab219",
  earnings: "#22b455",
};

const ET = "America/New_York";
const hm = (iso: string) => new Intl.DateTimeFormat("en-US", { timeZone: ET, hour: "numeric", minute: "2-digit" }).format(new Date(iso));

/**
 * The move day in 5-minute steps: stock and sector ETF, each as % vs the prior
 * close (one shared axis). Same-day stories are pinned where they were published,
 * so you can see whether the price moved before or after the news.
 */
export function IntradayChart({ c, onOpen }: { c: CaseView; onOpen: (e: Evidence) => void }) {
  const it = c.intraday;
  const t = c.ticker ?? "";
  const { rows, etf, pins, preOpen } = useMemo(() => {
    if (!it) return { rows: [], etf: null, pins: [], preOpen: [] };
    const etf = Object.keys(it.series).find((k) => k !== t) ?? null;
    const etfMap = new Map((etf ? it.series[etf] : []).map((p) => [p.t, p.pct]));
    const rows = (it.series[t] ?? []).map((p) => ({ t: p.t, [t]: p.pct * 100, ...(etf ? { [etf]: (etfMap.get(p.t) ?? NaN) * 100 } : {}) }));
    const starts = rows.map((r) => Date.parse(r.t));
    const pins: { x: string; y: number; e: Evidence }[] = [];
    const preOpen: Evidence[] = [];
    for (const e of c.evidence) {
      const m = e.meta?.price_move_at_publish as number | undefined;
      if (m === undefined || !e.published_at || isQuotePage(e)) continue;
      const ts = Date.parse(e.published_at);
      // last bar that had finished when the story came out
      let i = -1;
      for (let k = 0; k < starts.length; k++) if (starts[k] + 5 * 60000 <= ts) i = k;
      if (i < 0) preOpen.push(e);
      else pins.push({ x: rows[i].t, y: m * 100, e });
    }
    return { rows, etf, pins, preOpen };
  }, [it, c.evidence, t]);

  if (!it || !rows.length) return null;
  const s = it.summary;
  const shares = Object.entries(s.share_by).filter(([, v]) => v != null) as [string, number][];

  return (
    <div>
      <div className="card-h" style={{ marginBottom: 4 }}>
        <h3>Move day, minute by minute</h3>
        <span className="sub">{it.date} · % vs prior close · 5-minute prices</span>
      </div>
      <div className="id-stats">
        <span><b>{pct(s.open_pct, 1)}</b> first 5 min</span>
        <span><b>{pct(s.low_pct, 1)}</b> low at {s.low_at}</span>
        <span><b>{pct(s.close_pct, 1)}</b> close</span>
        {shares.map(([k, v]) => <span key={k}><b>{Math.round(v * 100)}%</b> of move by {k}</span>)}
      </div>
      <div className="legend">
        <span><i style={{ background: "var(--s-stock)", height: 2 }} />{t}</span>
        {etf && <span><i style={{ height: 0, borderTop: "2px dashed var(--s-sector)" }} />{etf} (sector)</span>}
        {pins.length > 0 && <span><i style={{ width: 10, height: 10, borderRadius: 5, background: "#e8eef8" }} /> Story published (click)</span>}
      </div>
      <ResponsiveContainer width="100%" height={230}>
        <LineChart data={rows} margin={{ top: 10, right: 14, bottom: 0, left: -6 }}>
          <CartesianGrid stroke="#1a2944" vertical={false} />
          <XAxis dataKey="t" tickFormatter={hm} tick={{ fill: "#7184a3", fontSize: 11 }} axisLine={{ stroke: "#1e2e4a" }} tickLine={false} minTickGap={50} />
          <YAxis tickFormatter={(v) => `${v.toFixed(0)}%`} domain={["auto", "auto"]} tick={{ fill: "#7184a3", fontSize: 11 }} axisLine={false} tickLine={false} width={44} />
          <ReferenceLine y={0} stroke="#3b5480" />
          <Tooltip
            cursor={{ stroke: "#3b5480" }}
            content={({ active, payload, label }) =>
              active && payload?.length ? (
                <div className="tooltip">
                  <div className="t">{hm(String(label))} ET</div>
                  {payload.map((p) => (
                    <div className="r" key={String(p.dataKey)}>
                      <span><span className="swatch" style={{ background: String(p.color) }} />{String(p.name)}</span>
                      <span className="mono">{typeof p.value === "number" && !Number.isNaN(p.value) ? pct(p.value / 100) : "—"}</span>
                    </div>
                  ))}
                </div>
              ) : null
            }
          />
          {etf && <Line type="monotone" dataKey={etf} name={`${etf} (sector)`} stroke="var(--s-sector)" strokeWidth={1.75} strokeDasharray="4 3" dot={false} isAnimationActive={false} connectNulls />}
          <Line type="monotone" dataKey={t} name={t} stroke="var(--s-stock)" strokeWidth={2.25} dot={false} isAnimationActive={false} />
          {pins.map((p) => (
            <ReferenceDot
              key={p.e.id}
              x={p.x}
              y={p.y}
              ifOverflow="extendDomain"
              shape={(props: { cx?: number; cy?: number }) => (
                <g style={{ cursor: "pointer" }} onClick={() => onOpen(p.e)}>
                  <title>{`${hm(p.e.published_at!)} ET · ${p.e.source}: ${p.e.title}`}</title>
                  <circle cx={props.cx} cy={props.cy} r={11} fill="transparent" />
                  <circle cx={props.cx} cy={props.cy} r={6} fill={KIND_COLOR[p.e.kind] ?? "#e8eef8"} stroke="#0f1a2e" strokeWidth={2} />
                </g>
              )}
            />
          ))}
        </LineChart>
      </ResponsiveContainer>
      {(pins.length > 0 || preOpen.length > 0) && (
        <div className="id-pins">
          {[...pins.map((p) => p.e), ...preOpen]
            .sort((a, b) => Date.parse(a.published_at!) - Date.parse(b.published_at!))
            .map((e) => {
              const share = e.meta?.share_of_move_at_publish as number | null | undefined;
              return (
                <button key={e.id} className="id-pin" onClick={() => onOpen(e)}>
                  <i style={{ background: KIND_COLOR[e.kind] ?? "#e8eef8" }} />
                  <span className="mono">{preOpen.includes(e) ? "Before open" : `${hm(e.published_at!)} ET`}</span>
                  <span className="id-pin-title">{e.title}</span>
                  <span className="id-pin-at">
                    {preOpen.includes(e) ? "before trading" : `${pct(e.meta?.price_move_at_publish as number, 1)}${share != null ? ` · ${Math.round(share * 100)}% done` : ""}`}
                  </span>
                </button>
              );
            })}
        </div>
      )}
      <p className="disclaimer" style={{ marginTop: 8 }}>
        Publication times come from the source and may lag the first report. A story that came out after most of the move had happened is unlikely to be its trigger.
      </p>
    </div>
  );
}

const UP_ICON: Record<string, JSX.Element> = {
  earnings: <DollarSign size={16} />,
  dividend: <Percent size={16} />,
  macro: <Landmark size={16} />,
};

export function UpcomingCard({ c }: { c: CaseView }) {
  const items = c.upcoming ?? [];
  if (!items.length) return null;
  const today = new Date(new Date().toLocaleString("en-US", { timeZone: ET }));
  today.setHours(12, 0, 0, 0);
  const days = (d: string) => Math.round((Date.parse(`${d}T12:00:00`) - today.getTime()) / 86400000);
  return (
    <div className="card">
      <div className="card-h"><CalendarDays size={16} /><h3>What to watch next</h3></div>
      <div className="up-list">
        {items.map((u: Upcoming) => {
          const n = days(u.date);
          const d = new Date(`${u.date}T12:00:00`);
          return (
            <div key={u.date + u.title} className={`up-item k-${u.kind}`}>
              <div className="up-date">
                <span className="m">{d.toLocaleDateString("en-US", { month: "short" })}</span>
                <span className="d">{d.getDate()}</span>
              </div>
              <div style={{ minWidth: 0 }}>
                <div className="up-title">{UP_ICON[u.kind]} {u.title}</div>
                <div className="up-detail">{n === 0 ? "Today" : n === 1 ? "Tomorrow" : `In ${n} days`} · {d.toLocaleDateString("en-US", { weekday: "short" })}. {u.detail}</div>
                {u.url && <a className="src-link" href={u.url} target="_blank" rel="noreferrer">Source</a>}
              </div>
            </div>
          );
        })}
      </div>
      <p className="disclaimer">Scheduled dates, not predictions. Dates can change.</p>
    </div>
  );
}
