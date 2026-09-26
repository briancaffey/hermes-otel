// Pure helpers for the search bar (#183): the filter shape and how it maps
// onto the live store's /live/traces and a backend adapter's /traces/search.
// No SDK import, so the vitest suite can load it.

export const LIVE = "live";

/** Append `backend=<name>` for a backend source (nothing for live). */
export function withBackend(params: URLSearchParams, source: string): URLSearchParams {
  if (source && source !== LIVE) params.set("backend", source);
  return params;
}

export type TraceFilters = {
  lookback: number;
  status: "" | "ok" | "error";
  kind: string;
  tool: string;
  model: string;
  session: string;
  minDurationMs: string;
  text: string;
  traceId: string;
  // backend-only, advanced
  q: string;
  service: string;
  rootsOnly: boolean;
};

export const DEFAULT_FILTERS: TraceFilters = {
  lookback: 1,
  status: "",
  kind: "",
  tool: "",
  model: "",
  session: "",
  minDurationMs: "",
  text: "",
  traceId: "",
  q: "",
  service: "",
  rootsOnly: true,
};

export function isDefaultFilters(f: TraceFilters): boolean {
  return Object.keys(DEFAULT_FILTERS).every((k) => (DEFAULT_FILTERS as any)[k] === (f as any)[k]);
}

export const KINDS = ["agent", "cron", "subagent", "tool", "llm", "api", "approval", "skill"];
// Backend adapters filter on span-name regex; kinds map to name prefixes.
const KIND_REGEX: Record<string, string> = {
  agent: "^agent",
  cron: "^cron",
  subagent: "^subagent",
  tool: "^tool\\.",
  llm: "^llm\\.",
  api: "^api\\.",
  approval: "^approval",
  skill: "^skill\\.",
};

export function liveParams(f: TraceFilters, limit = 100): URLSearchParams {
  const p = new URLSearchParams({ lookback_hours: String(f.lookback), limit: String(limit) });
  if (f.status) p.set("status", f.status);
  if (f.kind) p.set("kind", f.kind);
  if (f.tool.trim()) p.set("tool", f.tool.trim());
  if (f.model.trim()) p.set("model", f.model.trim());
  if (f.session.trim()) p.set("session", f.session.trim());
  if (Number(f.minDurationMs) > 0) p.set("min_duration_ms", String(Math.floor(Number(f.minDurationMs))));
  if (f.text.trim()) p.set("text", f.text.trim());
  if (f.traceId.trim()) p.set("trace_id", f.traceId.trim());
  return p;
}

export function backendParams(f: TraceFilters, source: string, limit = 50): URLSearchParams {
  const p = withBackend(new URLSearchParams({ lookback_hours: String(f.lookback), limit: String(limit) }), source);
  // A kind or tool filter matches non-root spans; widen to "any span" then.
  const rootsOnly = f.rootsOnly && !f.kind && !f.tool.trim();
  p.set("roots_only", String(rootsOnly));
  if (f.status) p.set("status", f.status);
  if (f.kind && KIND_REGEX[f.kind]) p.set("name_regex", KIND_REGEX[f.kind]);
  if (f.tool.trim()) p.set("tool", f.tool.trim());
  if (f.model.trim()) p.set("model", f.model.trim());
  if (f.session.trim()) p.set("session", f.session.trim());
  if (Number(f.minDurationMs) > 0) p.set("min_duration_ms", String(Math.floor(Number(f.minDurationMs))));
  if (f.text.trim()) p.set("free_text", f.text.trim());
  if (f.q.trim()) p.set("q", f.q.trim());
  if (f.service.trim()) p.set("service", f.service.trim());
  return p;
}


// ── logs tab (#186) ──────────────────────────────────────────────────────
export type LogFilters = { minLevel: string; logger: string; session: string; traceId: string; text: string; lookback: number };
export const DEFAULT_LOG_FILTERS: LogFilters = { minLevel: "0", logger: "", session: "", traceId: "", text: "", lookback: 1 };
export const LOG_PAGE_SIZES = [100, 200, 500, 1000];
export const DEFAULT_LOG_PAGE = 200;

/** Query for /logs/search: the filters, the page size and, past the first
 *  page, the keyset cursor (only rows strictly older than `beforeNs`). */
export function logParams(f: LogFilters, source: string, limit: number, beforeNs?: string | null): URLSearchParams {
  const p = withBackend(new URLSearchParams({ lookback_hours: String(f.lookback), limit: String(limit) }), source);
  if (Number(f.minLevel) > 0) p.set("min_level", f.minLevel);
  if (f.logger.trim()) p.set("logger", f.logger.trim());
  if (f.session.trim()) p.set("session", f.session.trim());
  if (f.traceId.trim()) p.set("trace_id", f.traceId.trim());
  if (f.text.trim()) p.set("text", f.text.trim());
  if (beforeNs && /^\d+$/.test(beforeNs)) p.set("before_ns", beforeNs);
  return p;
}

/** The URL's view of the filters (missing keys fall back to the defaults). */
export function logFiltersFromNav(nav: { level?: string; logger?: string; session?: string; trace?: string; text?: string; lookback?: string }): LogFilters {
  const lookback = Number(nav.lookback);
  return {
    minLevel: nav.level && /^\d+$/.test(nav.level) ? nav.level : DEFAULT_LOG_FILTERS.minLevel,
    logger: nav.logger || "",
    session: nav.session || "",
    traceId: nav.trace || "",
    text: nav.text || "",
    lookback: lookback > 0 ? lookback : DEFAULT_LOG_FILTERS.lookback,
  };
}

/** The filters as URL keys; an empty string clears a key (see navSearch). */
export function navFromLogFilters(f: LogFilters): { level: string; logger: string; session: string; trace: string; text: string; lookback: string } {
  return {
    level: Number(f.minLevel) > 0 ? f.minLevel : "",
    logger: f.logger.trim(),
    session: f.session.trim(),
    trace: f.traceId.trim(),
    text: f.text.trim(),
    lookback: f.lookback !== DEFAULT_LOG_FILTERS.lookback ? String(f.lookback) : "",
  };
}

export function logPageSizeFromNav(size?: string): number {
  const n = Number(size);
  return LOG_PAGE_SIZES.includes(n) ? n : DEFAULT_LOG_PAGE;
}
