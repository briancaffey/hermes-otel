// Pure helpers for the search bars (#183, #281, #284): the filter shapes, how
// they map onto the live store's /live/* queries and a backend adapter's
// /traces/search, and how they round-trip through the URL (nav.ts).
// No SDK import, so the vitest suite can load it.
import { contextWindow } from "./logs-lib";
import type { NavState } from "./nav";

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

/** One lookback list for every tab (hours). */
export const LOOKBACKS: { label: string; hours: number }[] = [
  { label: "15m", hours: 0.25 },
  { label: "1h", hours: 1 },
  { label: "6h", hours: 6 },
  { label: "24h", hours: 24 },
  { label: "3d", hours: 72 },
  { label: "7d", hours: 168 },
  { label: "30d", hours: 720 },
];

export const KINDS = ["agent", "cron", "subagent", "tool", "llm", "api", "approval", "skill", "session", "other"];
// A kind is a span-name prefix. The API takes `name_prefix` (contract §2);
// the live store takes `kind` directly.
export const KIND_PREFIX: Record<string, string> = {
  agent: "agent",
  cron: "cron",
  subagent: "subagent",
  tool: "tool.",
  llm: "llm.",
  api: "api.",
  approval: "approval",
  skill: "skill.",
  session: "session",
};

/** Query for /live/traces; `beforeNs` is the keyset cursor past the first page. */
export function liveParams(f: TraceFilters, limit = 50, beforeNs?: string | null): URLSearchParams {
  const p = new URLSearchParams({ lookback_hours: String(f.lookback), limit: String(limit) });
  if (f.status) p.set("status", f.status);
  if (f.kind) p.set("kind", f.kind);
  if (f.tool.trim()) p.set("tool", f.tool.trim());
  if (f.model.trim()) p.set("model", f.model.trim());
  if (f.session.trim()) p.set("session", f.session.trim());
  if (Number(f.minDurationMs) > 0) p.set("min_duration_ms", String(Math.floor(Number(f.minDurationMs))));
  if (f.text.trim()) p.set("text", f.text.trim());
  if (f.traceId.trim()) p.set("trace_id", f.traceId.trim());
  if (beforeNs && /^\d+$/.test(beforeNs)) p.set("before_ns", beforeNs);
  return p;
}

export function backendParams(f: TraceFilters, source: string, limit = 50, beforeNs?: string | null): URLSearchParams {
  const p = withBackend(new URLSearchParams({ lookback_hours: String(f.lookback), limit: String(limit) }), source);
  // A kind or tool filter matches non-root spans; widen to "any span" then.
  const rootsOnly = f.rootsOnly && !f.kind && !f.tool.trim();
  p.set("roots_only", String(rootsOnly));
  if (f.status) p.set("status", f.status);
  if (f.kind && KIND_PREFIX[f.kind]) p.set("name_prefix", KIND_PREFIX[f.kind]);
  if (f.tool.trim()) p.set("tool", f.tool.trim());
  if (f.model.trim()) p.set("model", f.model.trim());
  if (f.session.trim()) p.set("session", f.session.trim());
  if (Number(f.minDurationMs) > 0) p.set("min_duration_ms", String(Math.floor(Number(f.minDurationMs))));
  if (f.text.trim()) p.set("free_text", f.text.trim());
  if (f.q.trim()) p.set("q", f.q.trim());
  if (f.service.trim()) p.set("service", f.service.trim());
  if (beforeNs && /^\d+$/.test(beforeNs)) p.set("before_ns", beforeNs);
  return p;
}

/** The URL's view of the trace filters (missing keys fall back to the defaults). */
export function traceFiltersFromNav(nav: NavState): TraceFilters {
  const lookback = Number(nav.lookback);
  const status = nav.status === "ok" || nav.status === "error" ? nav.status : "";
  return {
    lookback: lookback > 0 ? lookback : nav.session || nav.trace ? 168 : DEFAULT_FILTERS.lookback,
    status,
    kind: nav.kind && KINDS.includes(nav.kind) ? nav.kind : "",
    tool: nav.tool || "",
    model: nav.model || "",
    session: nav.session || "",
    minDurationMs: nav.mindur && /^\d+$/.test(nav.mindur) ? nav.mindur : "",
    text: nav.text || "",
    traceId: "",
    q: nav.q || "",
    service: nav.service || "",
    rootsOnly: nav.roots !== "0",
  };
}

/** The trace filters as URL keys; an empty string clears a key (see navSearch). */
export function navFromTraceFilters(f: TraceFilters): NavState {
  return {
    status: f.status,
    kind: f.kind,
    tool: f.tool.trim(),
    model: f.model.trim(),
    session: f.session.trim(),
    mindur: Number(f.minDurationMs) > 0 ? String(Math.floor(Number(f.minDurationMs))) : "",
    text: f.text.trim(),
    q: f.q.trim(),
    service: f.service.trim(),
    roots: f.rootsOnly ? "" : "0",
    lookback: f.lookback !== DEFAULT_FILTERS.lookback ? String(f.lookback) : "",
  };
}

/** Which search fields the selected source honours (contract §1). */
export type FilterSupport = Record<string, "server" | "client" | "none">;
export const FILTER_FIELD_OF: Record<keyof TraceFilters, string> = {
  lookback: "lookback",
  status: "status_error",
  kind: "name",
  tool: "tool",
  model: "model",
  session: "session",
  minDurationMs: "min_duration",
  text: "free_text",
  traceId: "trace_id",
  q: "raw",
  service: "service",
  rootsOnly: "roots_only",
};
export function fieldSupport(support: FilterSupport | null | undefined, field: keyof TraceFilters): "server" | "client" | "none" | "unknown" {
  if (!support) return "unknown";
  const key = FILTER_FIELD_OF[field];
  const v = support[key];
  if (field === "status" && v === undefined) return support["status_ok"] ?? "unknown";
  return v ?? "unknown";
}

// ── logs tab (#186) ──────────────────────────────────────────────────────
export type LogFilters = {
  minLevel: string;
  logger: string;
  session: string;
  traceId: string;
  text: string;
  lookback: number;
  eventsOnly: boolean;
  /** one structured event name; implies eventsOnly */
  eventName: string;
  /** context window: unix ns of the line to centre on, "" = none */
  centerNs: string;
  /** half-width of the context window in seconds */
  windowS: number;
};
export const DEFAULT_LOG_FILTERS: LogFilters = {
  minLevel: "0",
  logger: "",
  session: "",
  traceId: "",
  text: "",
  lookback: 1,
  eventsOnly: false,
  eventName: "",
  centerNs: "",
  windowS: 30,
};
export const LOG_PAGE_SIZES = [100, 200, 500, 1000];
export const DEFAULT_LOG_PAGE = 200;
/** The API's `min_level` is on the Python scale; the rows show OTel names. */
export const LOG_LEVELS: { value: string; label: string; otel: number }[] = [
  { value: "0", label: "All levels", otel: 0 },
  { value: "10", label: "DEBUG+ (sev 5)", otel: 5 },
  { value: "20", label: "INFO+ (sev 9)", otel: 9 },
  { value: "30", label: "WARN+ (sev 13)", otel: 13 },
  { value: "40", label: "ERROR+ (sev 17)", otel: 17 },
];

/** Query for /logs/search: the filters, the page size and, past the first
 *  page, the keyset cursor (only rows strictly older than `beforeNs`). */
export function logParams(f: LogFilters, source: string, limit: number, beforeNs?: string | null): URLSearchParams {
  const p = withBackend(new URLSearchParams({ lookback_hours: String(f.lookback), limit: String(limit) }), source);
  if (Number(f.minLevel) > 0) p.set("min_level", f.minLevel);
  if (f.logger.trim()) p.set("logger", f.logger.trim());
  if (f.session.trim()) p.set("session", f.session.trim());
  if (f.traceId.trim()) p.set("trace_id", f.traceId.trim());
  if (f.text.trim()) p.set("text", f.text.trim());
  if (f.eventName.trim()) p.set("event_name", f.eventName.trim());
  else if (f.eventsOnly) p.set("events_only", "1");
  if (f.centerNs && /^\d+$/.test(f.centerNs)) {
    const { startS, endS } = contextWindow(Number(f.centerNs), f.windowS);
    p.set("start_s", String(startS));
    p.set("end_s", String(endS));
  }
  if (beforeNs && /^\d+$/.test(beforeNs)) p.set("before_ns", beforeNs);
  return p;
}

/** The URL's view of the filters (missing keys fall back to the defaults). */
export function logFiltersFromNav(nav: {
  level?: string;
  logger?: string;
  session?: string;
  trace?: string;
  text?: string;
  lookback?: string;
  events?: string;
  event?: string;
  center?: string;
  win?: string;
}): LogFilters {
  const lookback = Number(nav.lookback);
  return {
    minLevel: nav.level && /^\d+$/.test(nav.level) ? nav.level : DEFAULT_LOG_FILTERS.minLevel,
    logger: nav.logger || "",
    session: nav.session || "",
    traceId: nav.trace || "",
    text: nav.text || "",
    lookback: lookback > 0 ? lookback : DEFAULT_LOG_FILTERS.lookback,
    eventsOnly: nav.events === "1",
    eventName: nav.event || "",
    centerNs: nav.center && /^\d+$/.test(nav.center) ? nav.center : "",
    windowS: nav.win && /^\d+$/.test(nav.win) && Number(nav.win) > 0 ? Number(nav.win) : DEFAULT_LOG_FILTERS.windowS,
  };
}

/** The filters as URL keys; an empty string clears a key (see navSearch). */
export function navFromLogFilters(f: LogFilters): {
  level: string;
  logger: string;
  session: string;
  trace: string;
  text: string;
  lookback: string;
  events: string;
  event: string;
  center: string;
  win: string;
} {
  return {
    level: Number(f.minLevel) > 0 ? f.minLevel : "",
    logger: f.logger.trim(),
    session: f.session.trim(),
    trace: f.traceId.trim(),
    text: f.text.trim(),
    lookback: f.lookback !== DEFAULT_LOG_FILTERS.lookback ? String(f.lookback) : "",
    events: f.eventsOnly ? "1" : "",
    event: f.eventName.trim(),
    center: f.centerNs || "",
    win: f.centerNs && f.windowS !== DEFAULT_LOG_FILTERS.windowS ? String(f.windowS) : "",
  };
}

export function logPageSizeFromNav(size?: string): number {
  const n = Number(size);
  return LOG_PAGE_SIZES.includes(n) ? n : DEFAULT_LOG_PAGE;
}

/** The cursor stack in the URL: `before=<ns>,<ns>,…` newest page first. */
export function cursorsFromNav(before?: string): string[] {
  if (!before) return [];
  return before.split(",").filter((c) => /^\d+$/.test(c));
}
export function navFromCursors(cursors: string[]): string {
  return cursors.join(",");
}

// ── metrics tab (#284) ───────────────────────────────────────────────────
export const RANGES: { label: string; hours: number; bucket: number }[] = [
  { label: "15m", hours: 0.25, bucket: 15 },
  { label: "1h", hours: 1, bucket: 60 },
  { label: "6h", hours: 6, bucket: 300 },
  { label: "24h", hours: 24, bucket: 900 },
  { label: "3d", hours: 72, bucket: 3600 },
  { label: "7d", hours: 168, bucket: 3600 * 3 },
  { label: "30d", hours: 720, bucket: 3600 * 12 },
];
export const AGGS = ["sum", "count", "avg", "max", "last"];
export type ExplorerState = { range: (typeof RANGES)[number]; instrument: string; groupBy: string; agg: string };
export function explorerFromNav(nav: NavState): ExplorerState {
  const range = RANGES.find((r) => String(r.hours) === nav.range) || RANGES[1];
  return { range, instrument: nav.inst || "", groupBy: nav.group || "", agg: nav.agg && AGGS.includes(nav.agg) ? nav.agg : "sum" };
}
export function navFromExplorer(s: ExplorerState): NavState {
  return {
    range: s.range.hours !== RANGES[1].hours ? String(s.range.hours) : "",
    inst: s.instrument,
    group: s.groupBy,
    agg: s.agg !== "sum" ? s.agg : "",
  };
}
