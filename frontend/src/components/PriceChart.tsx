import { useMemo, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Line,
  LineChart,
  ReferenceDot,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { CaseView, Evidence, PricePoint } from "../lib/types";
import { KIND_COLOR } from "./MoveCharts";
import { pct } from "../lib/format";

const RANGES = { "1M": 22, "3M": 66, "6M": 130, "1Y": 260 } as const;
type Range = keyof typeof RANGES;

interface Series {
  key: string;
  label: string;
  color: string;
  dash?: string;
}

/**
 * Stock vs sector ETF vs S&P 500, each rebased to 100 at the start of the
 * window (one shared axis: no dual-axis chart). Volume lives in its own
 * small chart underneath.
 */
export function PriceChart({
  c, markers = [], focusId, onMarker,
}: { c: CaseView; markers?: Evidence[]; focusId?: string | null; onMarker?: (e: Evidence) => void }) {
  const [range, setRange] = useState<Range>("3M");
  const eventDate = c.anomaly?.event_date;

  const { rows, series } = useMemo(() => {
    const n = RANGES[range];
    const stock = c.prices.slice(-n);
    const start = stock[0]?.date;
    const series: Series[] = [{ key: c.ticker || "stock", label: c.ticker || "Stock", color: "var(--s-stock)" }];
    const others: [string, string, string, string?][] = [];
    if (c.sector_etf && c.benchmark_prices[c.sector_etf]) others.push([c.sector_etf, `${c.sector_etf} (sector)`, "var(--s-sector)"]);
    if (c.benchmark_prices["SPY"]) others.push(["SPY", "SPY (market)", "var(--s-market)", "4 3"]);

    const rebase = (pts: PricePoint[]) => {
      const inWin = pts.filter((p) => !start || p.date >= start);
      const base = inWin[0]?.close;
      return new Map(inWin.map((p) => [p.date, base ? (p.close / base) * 100 : null]));
    };
    const maps = others.map(([t]) => rebase(c.benchmark_prices[t]));
    const base0 = stock[0]?.close;
    const rows = stock.map((p) => {
      const r: Record<string, number | string | null | undefined> = {
        date: p.date,
        [series[0].key]: base0 ? (p.close / base0) * 100 : null,
        close: p.close,
        volume: p.volume,
      };
      others.forEach(([t], i) => (r[t] = maps[i].get(p.date) ?? null));
      return r;
    });
    others.forEach(([t, label, color, dash]) => series.push({ key: t, label, color, dash }));
    return { rows, series };
  }, [c, range]);

  // Pin each timeline event to the first trading day on/after its ET publish date.
  const pins = useMemo(() => {
    const out = new Map<string, Evidence[]>();
    if (!rows.length) return [] as { date: string; y: number; items: Evidence[] }[];
    const first = String(rows[0].date), last = String(rows[rows.length - 1].date);
    for (const e of markers) {
      if (!e.published_at) continue;
      const d = /^\d{4}-\d{2}-\d{2}$/.test(e.published_at)
        ? e.published_at
        : new Intl.DateTimeFormat("en-CA", { timeZone: "America/New_York", year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date(e.published_at));
      if (d < first) continue;
      const row = rows.find((r) => String(r.date) >= d) ?? (d <= last ? undefined : rows[rows.length - 1]);
      if (!row) continue;
      const k = String(row.date);
      out.set(k, [...(out.get(k) ?? []), e]);
    }
    const key = series[0].key;
    return [...out.entries()].map(([date, items]) => ({ date, y: Number(rows.find((r) => r.date === date)?.[key]), items }));
  }, [rows, markers, series]);

  if (!rows.length) return <div className="empty">Price history will appear once detection runs.</div>;
  const fmtDate = (d: string) => new Date(d + "T00:00:00Z").toLocaleDateString(undefined, { month: "short", day: "numeric", timeZone: "UTC" });

  return (
    <div>
      <div className="card-h" style={{ marginBottom: 4 }}>
        <h3>Price vs benchmarks</h3>
        <span className="sub">rebased to 100</span>
        <div className="spacer" />
        <div className="range-btns">
          {(Object.keys(RANGES) as Range[]).map((r) => (
            <button key={r} className={r === range ? "active" : ""} onClick={() => setRange(r)}>
              {r}
            </button>
          ))}
        </div>
      </div>
      <div className="legend">
        {series.map((s) => (
          <span key={s.key}>
            <i style={{ background: s.color, height: s.dash ? 0 : 2, borderTop: s.dash ? `2px dashed ${s.color}` : undefined }} />
            {s.label}
          </span>
        ))}
        {eventDate && (
          <span>
            <i style={{ background: "var(--warn)", width: 2, height: 12 }} /> Event day {eventDate}
          </span>
        )}
        {pins.length > 0 && (
          <span><i style={{ width: 10, height: 10, borderRadius: 5, background: "#e8eef8" }} /> Timeline events (click)</span>
        )}
      </div>
      <ResponsiveContainer width="100%" height={240}>
        <LineChart data={rows} margin={{ top: 8, right: 22, bottom: 0, left: -8 }}>
          <CartesianGrid stroke="#1a2944" vertical={false} />
          <XAxis dataKey="date" tickFormatter={fmtDate} tick={{ fill: "#7184a3", fontSize: 11 }} axisLine={{ stroke: "#1e2e4a" }} tickLine={false} minTickGap={40} />
          <YAxis domain={["auto", "auto"]} tick={{ fill: "#7184a3", fontSize: 11 }} axisLine={false} tickLine={false} width={44} />
          <Tooltip
            cursor={{ stroke: "#3b5480", strokeWidth: 1 }}
            content={({ active, payload, label }) =>
              active && payload?.length ? (
                <div className="tooltip">
                  <div className="t">{fmtDate(String(label))}</div>
                  {payload.map((p) => (
                    <div className="r" key={String(p.dataKey)}>
                      <span><span className="swatch" style={{ background: String(p.color) }} />{String(p.name)}</span>
                      <span className="mono">{typeof p.value === "number" ? pct(p.value / 100 - 1) : "—"}</span>
                    </div>
                  ))}
                  <div className="r" style={{ marginTop: 4 }}>
                    <span className="sub">{series[0].label} close</span>
                    <span className="mono">${Number(payload[0]?.payload?.close).toFixed(2)}</span>
                  </div>
                </div>
              ) : null
            }
          />
          {eventDate && <ReferenceLine x={eventDate} stroke="var(--warn)" strokeDasharray="3 3" />}
          {series.map((s) => (
            <Line
              key={s.key}
              type="monotone"
              dataKey={s.key}
              name={s.label}
              stroke={s.color}
              strokeWidth={s.key === series[0].key ? 2.25 : 1.75}
              strokeDasharray={s.dash}
              dot={false}
              activeDot={{ r: 4, stroke: "#0f1a2e", strokeWidth: 2 }}
              connectNulls
              isAnimationActive={false}
            />
          ))}
          {pins.map((p) => {
            const on = !!focusId && p.items.some((e) => e.id === focusId);
            return (
              <ReferenceDot
                key={p.date}
                x={p.date}
                y={p.y}
                ifOverflow="extendDomain"
                shape={(props: { cx?: number; cy?: number }) => (
                  <g style={{ cursor: "pointer" }} onClick={() => onMarker?.(p.items[0])}>
                    <title>{p.items.map((e) => `• ${e.title}`).join("\n")}</title>
                    <circle cx={props.cx} cy={props.cy} r={on ? 14 : 11} fill={on ? "rgba(141,184,255,.25)" : "transparent"} />
                    <circle cx={props.cx} cy={props.cy} r={on ? 8 : 6} fill={KIND_COLOR[p.items[0].kind] ?? "#e8eef8"} stroke="#0f1a2e" strokeWidth={2} />
                    {p.items.length > 1 && (
                      <text x={(props.cx ?? 0) + 9} y={(props.cy ?? 0) - 8} fill="#e8eef8" fontSize={10} fontWeight={700}>{p.items.length}</text>
                    )}
                  </g>
                )}
              />
            );
          })}
        </LineChart>
      </ResponsiveContainer>
      <div className="sub" style={{ margin: "4px 0 0 4px" }}>Volume</div>
      <ResponsiveContainer width="100%" height={56}>
        <BarChart data={rows} margin={{ top: 2, right: 12, bottom: 0, left: -8 }} barCategoryGap={1}>
          <XAxis dataKey="date" hide />
          <YAxis hide />
          <Tooltip
            cursor={{ fill: "rgba(255,255,255,.04)" }}
            content={({ active, payload, label }) =>
              active && payload?.length ? (
                <div className="tooltip">
                  <div className="t">{fmtDate(String(label))}</div>
                  <span className="mono">{Number(payload[0].value).toLocaleString()} shares</span>
                </div>
              ) : null
            }
          />
          <Bar dataKey="volume" fill="#2b4470" radius={[2, 2, 0, 0]} isAnimationActive={false} />
          {eventDate && <ReferenceLine x={eventDate} stroke="var(--warn)" strokeDasharray="3 3" />}
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

export function Sparkline({ values, width = 110, height = 32 }: { values: number[]; width?: number; height?: number }) {
  if (values.length < 2) return null;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const pts = values.map((v, i) => `${(i / (values.length - 1)) * width},${height - 2 - ((v - min) / span) * (height - 4)}`);
  const up = values[values.length - 1] >= values[0];
  return (
    <svg width={width} height={height} aria-hidden>
      <polyline points={pts.join(" ")} fill="none" stroke={up ? "#4ad07a" : "#ff7a72"} strokeWidth={1.75} strokeLinejoin="round" />
    </svg>
  );
}
