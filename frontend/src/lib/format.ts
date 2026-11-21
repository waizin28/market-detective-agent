export const pct = (x?: number | null, digits = 2) =>
  x === null || x === undefined || Number.isNaN(x) ? "—" : `${x >= 0 ? "+" : ""}${(x * 100).toFixed(digits)}%`;

export const num = (x?: number | null, digits = 2) =>
  x === null || x === undefined || Number.isNaN(x) ? "—" : x.toFixed(digits);

export const money = (x?: number | null) =>
  x === null || x === undefined ? "—" : `$${x.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

export const compact = (x?: number | null) => {
  if (x === null || x === undefined) return "—";
  const abs = Math.abs(x);
  if (abs >= 1e12) return `$${(x / 1e12).toFixed(2)}T`;
  if (abs >= 1e9) return `$${(x / 1e9).toFixed(2)}B`;
  if (abs >= 1e6) return `${(x / 1e6).toFixed(1)}M`;
  return x.toLocaleString();
};

export const dateTime = (iso?: string | null) => {
  if (!iso) return "time unknown";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  if (iso.length <= 10) return d.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" });
  return d.toLocaleString("en-US", {
    month: "short", day: "numeric", hour: "numeric", minute: "2-digit", timeZone: "America/New_York",
  }) + " ET";
};

export const ago = (iso?: string | null) => {
  if (!iso) return "";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
};

export const tone = (x?: number | null) => (x === null || x === undefined ? "" : x >= 0 ? "pos" : "neg");

export const CATEGORY_LABEL: Record<string, string> = {
  earnings_guidance: "Earnings / guidance",
  company_specific: "Company-specific",
  sector_wide: "Sector-wide",
  market_wide: "Market-wide",
  macro: "Macro",
  regulatory: "Regulatory",
  analyst_action: "Analyst action",
  corporate_action: "Corporate action",
  other: "Other",
};

export const SCOPE_LABEL: Record<string, string> = {
  company_specific: "Company-specific move",
  sector_wide: "Sector-wide move",
  market_wide: "Market-wide move",
  mixed: "Mixed move",
};
