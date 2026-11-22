export type Timing = "before" | "during" | "after" | "unknown";

export interface Evidence {
  id: string;
  kind: "sec_filing" | "news" | "market_data" | "macro_data" | "computed";
  title: string;
  source: string;
  url?: string | null;
  published_at?: string | null;
  date_only: boolean;
  retrieved_at: string;
  timing: Timing;
  snippet: string;
  flags: string[];
  meta: Record<string, unknown>;
}

export interface PeerMove {
  ticker: string;
  role: "market" | "sector" | "peer";
  ret: number | null;
  available: boolean;
}

export interface Anomaly {
  ticker: string;
  event_date: string;
  prev_date: string;
  close: number;
  prev_close: number;
  ret: number;
  mean_ret: number;
  std_ret: number;
  z_score: number;
  volume?: number | null;
  volume_ratio?: number | null;
  lookback_days: number;
  flagged: boolean;
  direction: "up" | "down";
  benchmarks: PeerMove[];
  excess_vs_market?: number | null;
  excess_vs_sector?: number | null;
  beta?: number | null;
  abnormal_ret?: number | null;
  abnormal_z?: number | null;
  scope: "company_specific" | "sector_wide" | "market_wide" | "mixed";
  data_issues: string[];
}

export interface Score {
  event: boolean;
  timing: boolean;
  price_fit: boolean;
  independent: boolean;
  decisive_contradiction: boolean;
  sources_conflict: boolean;
  total: number;
  supporting_ids: string[];
  contradicting_ids: string[];
  event_ids: string[];
  notes: string[];
  primary_sources: number;
}

export type BranchStatus = "active" | "pruned" | "ruled_out" | "finalist" | "leading" | "not_established";

export interface Branch {
  id: string;
  order: number;
  title: string;
  category: string;
  statement: string;
  prediction: string;
  would_weaken: string;
  status: BranchStatus;
  depth: number;
  score: Score | null;
  score_history: { round: number; total: number }[];
  refinements: string[];
  challenge_completed: boolean;
  notes: string[];
  critic_rationale?: string;
  rank?: number | null;
  blockers?: string[];
}

export interface Finding {
  branch_id: string;
  round: number;
  purpose: "initial" | "refine" | "challenge";
  question: string;
  status: "completed" | "failed";
  summary: string;
  outcome: "supports" | "contradicts" | "mixed" | "insufficient";
  supporting_evidence_ids: string[];
  contradicting_evidence_ids: string[];
  new_evidence_ids: string[];
}

export interface Explanation {
  branch_id: string;
  title: string;
  category: string;
  statement: string;
  status: BranchStatus;
  rank?: number | null;
  score: Score | null;
  narrative: string;
  blockers: string[];
  notes: string[];
}

export interface Verdict {
  status: "leading" | "unresolved" | "needs_review" | "no_anomaly";
  leading_ids: string[];
  withheld_ids?: string[];
  ranks: Record<string, number>;
  blockers: Record<string, string[]>;
}

export interface Report {
  headline: string;
  why_it_moved?: string;
  summary: string;
  rounds_run?: number;
  verdict: Verdict;
  explanations: Explanation[];
  uncertainties: string[];
  next_steps: string[];
  checks_used?: number;
  max_checks?: number;
  disclaimer: string;
  generated_at: string;
}

export interface AuditEvent {
  at: string;
  role: string;
  message: string;
  branch_id?: string;
}

export interface PricePoint {
  date: string;
  close: number;
  volume?: number | null;
}

export interface Interrupt {
  type: "clarify_security";
  question: string;
  candidates: { ticker: string; name: string }[];
}

export interface Message {
  role: "user" | "assistant";
  content: string;
  cited: string[];
  created_at: string;
}

export interface Profile {
  ticker: string;
  name?: string | null;
  sector?: string | null;
  industry?: string | null;
  market_cap?: number | null;
  pe_ratio?: number | null;
  week52_high?: number | null;
  week52_low?: number | null;
}

export interface CaseView {
  case_id: string;
  query: string;
  status: string;
  error?: string | null;
  ticker?: string;
  company?: string;
  profile?: Profile;
  sector_etf?: string | null;
  peers: string[];
  anomaly?: Anomaly | null;
  prices: PricePoint[];
  benchmark_prices: Record<string, PricePoint[]>;
  evidence: Evidence[];
  branches: Branch[];
  findings: Finding[];
  gaps: string[];
  events: AuditEvent[];
  round: number;
  checks_used: number;
  max_checks: number;
  report?: Report | null;
  interrupt?: Interrupt | null;
  api_calls?: number | null;
  running: boolean;
  messages?: Message[];
}

export interface CaseSummary {
  id: string;
  query: string;
  ticker?: string | null;
  company?: string | null;
  status: string;
  created_at: string;
  headline?: string | null;
  verdict?: string | null;
  event_date?: string | null;
  ret?: number | null;
  z?: number | null;
  error?: string | null;
}

export interface ScanRow {
  ticker: string;
  last_close?: number;
  last_date?: string;
  last_ret?: number;
  event_date?: string;
  event_ret?: number;
  z_score?: number;
  flagged?: boolean;
  vs_market?: string;
  volume_ratio?: number | null;
  spark?: number[];
  error?: string;
}

export interface AppConfig {
  model: string;
  keys: Record<string, boolean>;
  limits: Record<string, number>;
}
