import type { AppConfig, CaseSummary, CaseView, Message, ScanRow } from "./types";

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
  });
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* non-JSON error */
    }
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

export const api = {
  config: () => req<AppConfig>("/api/config"),
  createCase: (body: { query: string; event_date?: string | null; peers?: string[] | null; force?: boolean }) =>
    req<{ case_id: string }>("/api/cases", { method: "POST", body: JSON.stringify(body) }),
  getCase: (id: string) => req<CaseView>(`/api/cases/${id}`),
  listCases: () => req<CaseSummary[]>("/api/cases"),
  resume: (id: string, value: string) =>
    req<{ ok: boolean }>(`/api/cases/${id}/resume`, { method: "POST", body: JSON.stringify({ value }) }),
  ask: (id: string, question: string) =>
    req<Message>(`/api/cases/${id}/ask`, { method: "POST", body: JSON.stringify({ question }) }),
  scan: (tickers?: string[]) => req<ScanRow[]>(`/api/scan${tickers?.length ? `?tickers=${tickers.join(",")}` : ""}`),
  watchlist: () => req<string[]>("/api/watchlist"),
  addWatch: (ticker: string) => req<string[]>("/api/watchlist", { method: "POST", body: JSON.stringify({ ticker }) }),
  removeWatch: (ticker: string) => req<string[]>(`/api/watchlist/${ticker}`, { method: "DELETE" }),
};

export type StreamEvent =
  | { type: "progress"; role: string; message: string; at: string; seq: number }
  | { type: "snapshot"; case: CaseView; seq: number }
  | { type: "interrupt"; payload: unknown; seq: number }
  | { type: "done"; status: string; seq: number }
  | { type: "error"; message: string; seq: number };

/** Subscribe to a case's live events. Returns an unsubscribe function. */
export function subscribe(caseId: string, onEvent: (e: StreamEvent) => void, since = 0): () => void {
  let closed = false;
  let last = since - 1;
  let es: EventSource | null = null;

  const open = () => {
    es = new EventSource(`/api/cases/${caseId}/events?since=${last + 1}`);
    for (const type of ["progress", "snapshot", "interrupt", "done", "error"]) {
      es.addEventListener(type, (raw) => {
        const ev = JSON.parse((raw as MessageEvent).data) as StreamEvent;
        if (typeof ev.seq === "number") last = Math.max(last, ev.seq);
        onEvent(ev);
        if (ev.type === "done") es?.close();
      });
    }
    es.onerror = () => {
      es?.close();
      if (!closed) setTimeout(open, 2000); // reconnect and replay from the last seq
    };
  };
  open();
  return () => {
    closed = true;
    es?.close();
  };
}
