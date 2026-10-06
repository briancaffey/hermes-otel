// Shared helpers — ported in full from the original dashboard + live-mode adds.
// Trace (backend) data is OTLP/Tempo-shaped; live data is the compact dict the
// in-process store emits. Both flow through the same rendering.

// ── OTLP attribute decoding (backend traces) ─────────────────────────────
export function decodeAttrValue(v: any): any {
  if (v == null) return null;
  if (typeof v !== "object") return v;
  if ("stringValue" in v) return v.stringValue;
  if ("intValue" in v) return Number(v.intValue);
  if ("doubleValue" in v) return v.doubleValue;
  if ("boolValue" in v) return v.boolValue;
  if ("arrayValue" in v) return (v.arrayValue.values || []).map(decodeAttrValue);
  if ("kvlistValue" in v) return attrsObject(v.kvlistValue.values);
  return JSON.stringify(v);
}
export function attrsObject(attrs: any[]): Record<string, any> {
  const out: Record<string, any> = {};
  for (const a of attrs || []) if (a && a.key) out[a.key] = decodeAttrValue(a.value);
  return out;
}

// ── formatting ───────────────────────────────────────────────────────────
export function fmtDurationMs(ms: number | null | undefined): string {
  if (ms == null) return "—";
  if (ms < 1) return `${(ms * 1000).toFixed(0)}µs`;
  if (ms < 1000) return `${ms.toFixed(ms < 10 ? 1 : 0)}ms`;
  if (ms < 60000) return `${(ms / 1000).toFixed(2)}s`;
  const m = Math.floor(ms / 60000);
  return `${m}m ${Math.round((ms % 60000) / 1000)}s`;
}
const TIME_FMT: Intl.DateTimeFormatOptions = {
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hour12: false,
};
const CLOCK_FMT: Intl.DateTimeFormatOptions = { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false };
/** Local time as `YYYY-MM-DD HH:MM:SS` (one shape in every locale, #289). */
export function fmtAbsTime(unixNano: number): string {
  if (!unixNano) return "";
  try {
    const d = new Date(unixNano / 1e6);
    const p = (n: number) => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
  } catch {
    return "";
  }
}
/** Local clock time `HH:MM:SS` (chart axes). */
export function fmtClock(unixNano: number): string {
  if (!unixNano) return "";
  try {
    return new Date(unixNano / 1e6).toLocaleTimeString(undefined, CLOCK_FMT);
  } catch {
    return "";
  }
}
/** The browser's timezone name, shown once per page next to absolute times. */
export function localTimezone(): string {
  try {
    return Intl.DateTimeFormat(undefined, TIME_FMT).resolvedOptions().timeZone || "";
  } catch {
    return "";
  }
}
export function fmtTimeAgo(unixNano: number): string {
  if (!unixNano) return "";
  const diff = Date.now() - unixNano / 1e6;
  if (diff < 1500) return "just now";
  if (diff < 60000) return `${Math.round(diff / 1000)}s ago`;
  if (diff < 3600000) return `${Math.round(diff / 60000)}m ago`;
  if (diff < 86400000) return `${Math.round(diff / 3600000)}h ago`;
  return `${Math.round(diff / 86400000)}d ago`;
}
export function fmtTokens(n: any): string | null {
  if (n == null || isNaN(Number(n))) return null;
  const num = Number(n);
  if (num >= 10000) return `${(num / 1000).toFixed(num >= 100000 ? 0 : 1)}k`;
  return num.toLocaleString("en-US");
}
/** A cost for a tile: unknown reads as an em dash, never `$0` (#280). */
export function fmtCost(usd: number | null | undefined): string {
  if (usd == null || isNaN(Number(usd))) return "—";
  if (usd === 0) return "$0.00";
  if (usd < 0.01) return `$${usd.toFixed(4)}`;
  return `$${usd.toFixed(2)}`;
}
/** A cost on a card or header: four decimals, unknown as em dash. */
export function fmtCostExact(usd: number | null | undefined): string {
  if (usd == null || isNaN(Number(usd))) return "—";
  return `$${Number(usd).toFixed(4)}`;
}
export function fmtInt(n: number | null | undefined): string {
  return n == null ? "—" : n.toLocaleString("en-US");
}
export function clip(s: any, max: number): string | null {
  if (s == null) return null;
  const str = (typeof s === "string" ? s : String(s)).replace(/\s+/g, " ").trim();
  if (!str) return null;
  return str.length <= max ? str : str.slice(0, max - 1) + "…";
}

// ── span-kind classification (drives icon + accent) ──────────────────────
export type Kind = "agent" | "llm" | "api" | "tool" | "skill" | "approval" | "subagent" | "session" | "cron" | "other";

export function kindOf(name: string, attrs?: Record<string, any>): Kind {
  const a = attrs || {};
  if (a["hermes.span_kind"] === "skill") return "skill";
  if (a["hermes.span_kind"] === "approval") return "approval";
  const n = (name || "").toLowerCase();
  if (n === "agent" || n.startsWith("agent.")) return "agent";
  if (n === "cron" || n.startsWith("cron")) return "cron";
  if (n.startsWith("session")) return "session";
  if (n.startsWith("skill.")) return "skill";
  if (n.startsWith("approval")) return "approval";
  if (n.startsWith("subagent")) return "subagent";
  if (n.startsWith("llm.")) return "llm";
  if (n.startsWith("api.")) return "api";
  if (n.startsWith("tool.")) return "tool";
  return "other";
}

// Kind colours are CSS custom properties defined in dist/style.css (with a
// light-scheme block), so SVG fills, inline styles and the `otel-c-*` text
// classes cannot drift apart and follow the host theme (#289).
export const KIND_COLOR: Record<Kind, string> = {
  agent: "var(--otel-kind-agent)",
  llm: "var(--otel-kind-llm)",
  api: "var(--otel-kind-api)",
  tool: "var(--otel-kind-tool)",
  skill: "var(--otel-kind-skill)",
  approval: "var(--otel-kind-approval)",
  subagent: "var(--otel-kind-subagent)",
  session: "var(--otel-kind-agent)",
  cron: "var(--otel-kind-cron)",
  other: "var(--otel-kind-other)",
};

// ── trace-level attribute merge (backend: TraceQL select across spanSet) ──
export function traceAttrs(trace: any): Record<string, any> {
  const out: Record<string, any> = {};
  const spanSets = trace.spanSets || (trace.spanSet ? [trace.spanSet] : []);
  if (!spanSets.length) return out;
  const spans = spanSets[0].spans || [];
  const priority = (s: any) => {
    const n = (s.name || "").toLowerCase();
    if (n.startsWith("api.")) return 0;
    if (n.startsWith("tool.")) return 1;
    if (n.startsWith("llm.")) return 2;
    return 3;
  };
  for (const sp of spans.slice().sort((a: any, b: any) => priority(a) - priority(b))) {
    for (const a of sp.attributes || []) {
      if (!a.key || out[a.key] != null) continue;
      const d = decodeAttrValue(a.value);
      if (d !== null && d !== undefined && d !== "") out[a.key] = d;
    }
  }
  return out;
}
// Whole-trace span count. Adapters that know it send ``spanCount``; Tempo
// sends per-service stats. ``spanSets[0].spans`` is the MATCHED spans (one
// per trace in roots-only mode), never the trace size, so it is not a
// fallback (#179): no number beats a wrong one.
export function traceSpanCount(trace: any): number | null {
  if (typeof trace.spanCount === "number" && trace.spanCount > 0) return trace.spanCount;
  if (typeof trace.span_count === "number" && trace.span_count > 0) return trace.span_count;
  if (trace.serviceStats) {
    let total = 0;
    for (const k in trace.serviceStats) total += trace.serviceStats[k].spanCount || 0;
    if (total) return total;
  }
  return null;
}

// ── input/output preview extraction (parses the messages JSON) ───────────
export function extractInputPreview(attrs: Record<string, any>): string | null {
  const raw = attrs["input.value"];
  if (raw == null) return null;
  if (typeof raw === "string") {
    const t = raw.trim();
    if (t[0] === "[" || t[0] === "{") {
      try {
        const parsed = JSON.parse(t);
        if (Array.isArray(parsed)) {
          for (let i = parsed.length - 1; i >= 0; i--) {
            const m = parsed[i];
            if (m && m.role === "user") {
              const c = m.content;
              if (typeof c === "string") return c;
              if (Array.isArray(c)) {
                const parts: string[] = [];
                for (const p of c) {
                  if (typeof p === "string") parts.push(p);
                  else if (p && typeof p.text === "string") parts.push(p.text);
                }
                if (parts.length) return parts.join(" ");
              }
              if (c != null) return JSON.stringify(c);
            }
          }
          for (const m of parsed) if (m && typeof m.content === "string") return m.content;
        }
        return t;
      } catch {
        return raw;
      }
    }
    return raw;
  }
  return String(raw);
}
export function extractOutputPreview(attrs: Record<string, any>): string | null {
  return attrs["llm.output.content"] || attrs["output.value"] || null;
}

// ── span tree (backend OTLP detail) ──────────────────────────────────────
export type TreeSpan = {
  spanId: string;
  parentSpanId: string | null;
  name: string;
  startNs: number;
  endNs: number;
  durationMs: number;
  status: any;
  _attrs: Record<string, any>;
  children: TreeSpan[];
};
export function buildSpanTree(batches: any[]): { roots: TreeSpan[]; all: TreeSpan[] } {
  if (!batches || !batches.length) return { roots: [], all: [] };
  const all: TreeSpan[] = [];
  for (const b of batches) {
    const scopeSpans = b.scopeSpans || b.scope_spans || b.instrumentationLibrarySpans || [];
    for (const ss of scopeSpans) {
      for (const span of ss.spans || []) {
        const startNs = Number(span.startTimeUnixNano || span.start_time_unix_nano || 0);
        const endNs = Number(span.endTimeUnixNano || span.end_time_unix_nano || 0);
        all.push({
          spanId: span.spanId || span.span_id,
          parentSpanId: span.parentSpanId || span.parent_span_id || null,
          name: span.name,
          startNs,
          endNs,
          durationMs: endNs && startNs ? (endNs - startNs) / 1e6 : 0,
          status: span.status || null,
          _attrs: attrsObject(span.attributes),
          children: [],
        });
      }
    }
  }
  return linkTree(all);
}
/** Parent/child links from a flat list; orphans become roots, siblings sort by start. */
export function linkTree(all: TreeSpan[]): { roots: TreeSpan[]; all: TreeSpan[] } {
  const byId: Record<string, TreeSpan> = {};
  all.forEach((s) => (byId[s.spanId] = s));
  const roots: TreeSpan[] = [];
  all.forEach((s) => {
    if (s.parentSpanId && byId[s.parentSpanId]) byId[s.parentSpanId].children.push(s);
    else roots.push(s);
  });
  const sortRec = (list: TreeSpan[]) => {
    list.sort((a, b) => a.startNs - b.startNs);
    list.forEach((n) => sortRec(n.children));
  };
  sortRec(roots);
  return { roots, all };
}
export function flatten(roots: TreeSpan[]): { span: TreeSpan; depth: number }[] {
  const out: { span: TreeSpan; depth: number }[] = [];
  const walk = (n: TreeSpan, depth: number) => {
    out.push({ span: n, depth });
    n.children.forEach((c) => walk(c, depth + 1));
  };
  roots.forEach((r) => walk(r, 0));
  return out;
}
export function statusCode(status: any): "ok" | "error" | null {
  if (!status) return null;
  const code = status.code ?? status.statusCode;
  if (code === 2 || code === "STATUS_CODE_ERROR" || status === "ERROR") return "error";
  if (code === 1 || code === "STATUS_CODE_OK" || status === "OK") return "ok";
  return null;
}

// ── live-store span helpers (compact dict shape) ─────────────────────────
export type LiveSpan = {
  trace_id: string;
  span_id: string;
  parent_span_id: string | null;
  name: string;
  start_time_unix_nano: number;
  end_time_unix_nano: number;
  duration_ms: number | null;
  status: string;
  attributes: Record<string, any>;
  seq: number;
};
export function attrNum(a: Record<string, any>, ...keys: string[]): number | null {
  for (const k of keys) {
    const v = a[k];
    if (typeof v === "number") return v;
    if (typeof v === "string" && v.trim() && !isNaN(Number(v))) return Number(v);
  }
  return null;
}
export const liveTokens = (s: LiveSpan) => attrNum(s.attributes, "gen_ai.usage.total_tokens", "llm.token_count.total");
export const liveCost = (s: LiveSpan) => attrNum(s.attributes, "hermes.cost.usage");
// The requested model is the one shown everywhere (cards, header, metrics);
// the response model appears next to it in the header when it differs (#185).
export const liveModel = (s: LiveSpan) =>
  s.attributes["gen_ai.request.model"] || s.attributes["llm.model_name"] || s.attributes["gen_ai.response.model"] || null;
export const sessionOf = (s: LiveSpan) => s.attributes["hermes.session_id"] || s.attributes["session_id"] || s.attributes["session.id"] || null;

/** The root of a flat span list: the first span whose parent is absent, else null (a trace still in flight). */
export function findRoot<T extends { span_id?: string; spanId?: string; parent_span_id?: string | null; parentSpanId?: string | null }>(spans: T[]): T | null {
  const ids = new Set(spans.map((s) => s.span_id ?? s.spanId));
  return spans.find((s) => !(s.parent_span_id ?? s.parentSpanId) || !ids.has((s.parent_span_id ?? s.parentSpanId) as string)) || null;
}

// ── assemble TRACES from flat live spans (so the live store powers a real ──
// trace browser + waterfall, no external backend needed) ──────────────────
// One trace-list row. Built in the browser from buffered spans
// (groupLiveTraces, which fills root/spans) or returned by the live store's
// /live/traces query (#184), where the spans come later from /live/traces/{id}.
export type LiveTrace = {
  traceId: string;
  root?: LiveSpan;
  rootName: string;
  rootKind: Kind;
  service: string;
  startNs: number;
  endNs: number;
  durationMs: number;
  spanCount: number;
  model: string | null;
  tokens: number | null;
  cost: number | null;
  error: boolean;
  session: string | null;
  spans?: LiveSpan[];
  turn?: number | null;
  platform?: string | null;
  /** contract §4/§8: the root span has not finished yet */
  partial?: boolean;
};

/** One row shape for the trace list, whatever the source (#281). */
export type TraceRow = {
  traceId: string;
  rootName: string;
  rootKind: Kind;
  service: string | null;
  startNs: number;
  endNs: number;
  durationMs: number;
  spanCount: number | null;
  model: string | null;
  tokens: number | null;
  cost: number | null;
  error: boolean;
  session: string | null;
  partial: boolean;
  toolName: string | null;
  inPreview: string | null;
  outPreview: string | null;
  /** the source's own record, for the detail view */
  raw: any;
};

export function rowFromLive(t: LiveTrace): TraceRow {
  return {
    traceId: String(t.traceId),
    rootName: t.rootName,
    rootKind: t.rootKind || kindOf(t.rootName),
    service: t.service || null,
    startNs: t.startNs,
    endNs: t.endNs || t.startNs,
    durationMs: t.durationMs,
    spanCount: t.spanCount ?? null,
    model: t.model,
    tokens: t.tokens,
    cost: t.cost,
    error: !!t.error,
    session: t.session,
    partial: !!t.partial,
    toolName: null,
    inPreview: null,
    outPreview: null,
    raw: t,
  };
}

export function rowFromBackend(t: any): TraceRow {
  const attrs = traceAttrs(t);
  const startNs = t.startTimeUnixNano ? Number(t.startTimeUnixNano) : 0;
  const durationMs = Number(t.durationMs || 0);
  const name = t.rootTraceName || "";
  return {
    traceId: String(t.traceID || t.traceId || ""),
    rootName: name || "—",
    rootKind: kindOf(name, attrs),
    service: t.rootServiceName || null,
    startNs,
    endNs: startNs + durationMs * 1e6,
    durationMs,
    spanCount: traceSpanCount(t),
    model: attrs["gen_ai.request.model"] || attrs["llm.model_name"] || attrs["gen_ai.response.model"] || null,
    tokens: attrNum(attrs, "gen_ai.usage.total_tokens", "llm.token_count.total"),
    cost: attrNum(attrs, "hermes.cost.usage"),
    error: attrs["status"] === "error" || !!attrs["error.type"],
    session: sessionOfCard(t),
    partial: false,
    toolName: attrs["tool.name"] ? String(attrs["tool.name"]) : null,
    inPreview: clip(extractInputPreview(attrs), 140),
    outPreview: clip(extractOutputPreview(attrs), 140),
    raw: t,
  };
}

// ── sessions: group trace rows by session id (#187) ──────────────────────
export type SessionRow = {
  session: string;
  turns: number;
  spans: number | null;
  errors: number;
  tokens: number | null;
  cost: number | null;
  toolCalls: number | null;
  startNs: number;
  endNs: number;
  model: string | null;
  platform: string | null;
  traceIds: string[];
};

// Session id of a BACKEND card (adapters put it on the card attributes).
export function sessionOfCard(trace: any): string | null {
  const a = traceAttrs(trace);
  return a["hermes.session_id"] || a["langfuse.sessionId"] || a["session.id"] || a["session_id"] || null;
}

// Group backend search results client-side. Tokens/cost come from each card's
// root attributes (already the turn's totals), so nothing is counted twice.
export function groupBySession(traces: any[]): SessionRow[] {
  const by: Record<string, SessionRow> = {};
  for (const t of traces) {
    const sid = sessionOfCard(t);
    if (!sid) continue;
    const a = traceAttrs(t);
    const start = Number(t.startTimeUnixNano || 0);
    const end = start + Number(t.durationMs || 0) * 1e6;
    const tok = attrNum(a, "gen_ai.usage.total_tokens", "llm.token_count.total");
    const cost = attrNum(a, "hermes.cost.usage");
    const spanCount = traceSpanCount(t);
    const row = (by[sid] ||= {
      session: sid,
      turns: 0,
      spans: 0,
      errors: 0,
      tokens: null,
      cost: null,
      toolCalls: null,
      startNs: start,
      endNs: end,
      model: a["llm.model_name"] || a["gen_ai.request.model"] || null,
      platform: a["hermes.platform"] || null,
      traceIds: [],
    });
    row.turns += 1;
    row.spans = spanCount == null || row.spans == null ? null : row.spans + spanCount;
    if (a["status"] === "error" || a["error.type"]) row.errors += 1;
    if (tok != null) row.tokens = (row.tokens || 0) + tok;
    if (cost != null) row.cost = (row.cost || 0) + cost;
    row.startNs = Math.min(row.startNs, start);
    row.endNs = Math.max(row.endNs, end);
    row.traceIds.push(t.traceID || t.traceId);
  }
  return Object.values(by).sort((x, y) => y.endNs - x.endNs);
}

// Token and cost totals for ONE trace. The ``agent`` root already carries the
// turn's totals and every ``api.*`` span carries its own call, so summing all
// spans counted each turn twice (#178). Use the root's figure when it has one;
// otherwise sum the ``api.*`` spans only (``llm.*`` spans mirror the API spans).
// The same rule serves the header (#283): `sumApiSpans` is the one fallback.
export type NamedAttrs = { name: string; attributes: Record<string, any> };
export function sumApiSpans(spans: NamedAttrs[], ...keys: string[]): number | null {
  let sum = 0;
  let seen = false;
  for (const s of spans) {
    if (!(s.name || "").startsWith("api.")) continue;
    const v = attrNum(s.attributes || {}, ...keys);
    if (v != null) {
      sum += v;
      seen = true;
    }
  }
  return seen ? sum : null;
}
export function traceTotals(spans: LiveSpan[]): { tokens: number | null; cost: number | null } {
  const root = findRoot(spans);
  const named = spans.map((s) => ({ name: s.name, attributes: s.attributes }));
  const pick = (keys: string[], get: (s: LiveSpan) => number | null): number | null => {
    if (root) {
      const v = get(root);
      if (v != null) return v;
    }
    return sumApiSpans(named, ...keys);
  };
  // A root that reports a cost status without an amount (unknown, included or
  // partial pricing) means "no total": never fall back to a subtotal (#252).
  const noCostTotal = root != null && root.attributes["hermes.cost.status"] != null && liveCost(root) == null;
  return { tokens: pick(["gen_ai.usage.total_tokens", "llm.token_count.total"], liveTokens), cost: noCostTotal ? null : pick(["hermes.cost.usage"], liveCost) };
}

export function groupLiveTraces(spans: LiveSpan[]): LiveTrace[] {
  const byTrace: Record<string, LiveSpan[]> = {};
  for (const s of spans) (byTrace[s.trace_id] ||= []).push(s);
  const out: LiveTrace[] = [];
  for (const tid in byTrace) {
    const ss = byTrace[tid];
    const found = findRoot(ss);
    const root = found || ss[0];
    const startNs = Math.min(...ss.map((s) => s.start_time_unix_nano || 0));
    const endNs = Math.max(...ss.map((s) => s.end_time_unix_nano || s.start_time_unix_nano || 0));
    const totals = traceTotals(ss);
    let model: string | null = null;
    let error = false;
    for (const s of ss) {
      if (!model) model = liveModel(s);
      if (s.status === "ERROR") error = true;
    }
    out.push({
      traceId: tid,
      root,
      rootName: root.name,
      rootKind: kindOf(root.name, root.attributes),
      service: root.attributes["service.name"] || "hermes",
      startNs,
      endNs,
      durationMs: (endNs - startNs) / 1e6,
      spanCount: ss.length,
      model,
      tokens: totals.tokens,
      cost: totals.cost,
      error,
      session: sessionOf(root),
      spans: ss,
      partial: !found,
    });
  }
  return out.sort((a, b) => b.startNs - a.startNs);
}

// MCP keepalive pings (issue #62). MCP SDK 2.x records a one-span
// "MCP send ping" trace per keepalive; the plugin drops the successful ones by
// default (suppress_mcp_ping_spans), but a user who turns that off still wants
// them hidden in the list unless asked. Failed pings are never hidden.
export const MCP_PING_NAME = "MCP send ping";
export function isMcpKeepalivePing(rootName: string | null | undefined, error: boolean): boolean {
  return rootName === MCP_PING_NAME && !error;
}

// Live spans → the same TreeSpan shape the backend waterfall renders.
export function liveTreeFromSpans(spans: LiveSpan[]): { roots: TreeSpan[]; all: TreeSpan[] } {
  const all: TreeSpan[] = spans.map((s) => ({
    spanId: s.span_id,
    parentSpanId: s.parent_span_id || null,
    name: s.name,
    startNs: s.start_time_unix_nano || 0,
    endNs: s.end_time_unix_nano || s.start_time_unix_nano || 0,
    durationMs: s.duration_ms || 0,
    status: s.status === "ERROR" ? { code: 2 } : null,
    _attrs: s.attributes || {},
    children: [],
  }));
  return linkTree(all);
}

// ── trace detail helpers (#185) ──────────────────────────────────────────

// The duplicate conventions the plugin emits for one fact. The first key
// present is shown; the others are folded under it in "all attributes".
const DUPLICATE_GROUPS: string[][] = [
  ["gen_ai.request.model", "llm.model_name"],
  ["gen_ai.usage.input_tokens", "llm.token_count.prompt"],
  ["gen_ai.usage.output_tokens", "llm.token_count.completion"],
  ["gen_ai.usage.total_tokens", "llm.token_count.total"],
  ["gen_ai.usage.reasoning.output_tokens", "llm.token_count.completion_details.reasoning"],
  ["gen_ai.usage.cache_read.input_tokens", "gen_ai.usage.cache_read_input_tokens", "llm.token_count.prompt_details.cache_read"],
  ["gen_ai.provider.name", "gen_ai.system", "llm.provider"],
  ["hermes.session_id", "session.id", "session_id", "gen_ai.conversation.id", "wandb.thread_id"],
  ["openinference.span.kind", "traceloop.span.kind"],
];

export type AttrGroup = { prefix: string; entries: { key: string; value: any; aliases: string[] }[] };

// Attributes grouped by prefix (gen_ai, llm, hermes, tool, …), duplicate
// conventions folded, long content keys left to the summary.
export function groupAttrs(attrs: Record<string, any>): AttrGroup[] {
  const folded = new Set<string>();
  const alias: Record<string, string[]> = {};
  for (const grp of DUPLICATE_GROUPS) {
    const present = grp.filter((k) => attrs[k] !== undefined && attrs[k] !== null && attrs[k] !== "");
    if (present.length > 1) {
      alias[present[0]] = present.slice(1);
      present.slice(1).forEach((k) => folded.add(k));
    }
  }
  const groups: Record<string, AttrGroup> = {};
  for (const key of Object.keys(attrs).sort()) {
    if (folded.has(key)) continue;
    const prefix = key.includes(".") ? key.split(".")[0] : "other";
    (groups[prefix] ||= { prefix, entries: [] }).entries.push({ key, value: attrs[key], aliases: alias[key] || [] });
  }
  const order = ["hermes", "gen_ai", "llm", "tool", "input", "output", "session", "openinference"];
  return Object.values(groups).sort((a, b) => {
    const ia = order.indexOf(a.prefix);
    const ib = order.indexOf(b.prefix);
    return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib) || a.prefix.localeCompare(b.prefix);
  });
}

export type HeaderFacts = {
  requestModel: string | null;
  responseModel: string | null;
  inputTokens: number | null;
  outputTokens: number | null;
  reasoningTokens: number | null;
  cacheReadTokens: number | null;
  totalTokens: number | null;
  cost: number | null;
  /** true when the root says a cost status without an amount (#252): show "no pricing data", not a subtotal */
  costUnknown: boolean;
  tools: string[];
  exitReason: string | null;
  finalStatus: string | null;
  session: string | null;
  turn: number | null;
  platform: string | null;
};

// Facts for the trace header, read from the ROOT span's attributes (the
// turn's totals live there), with the api.* spans as the fallback for tokens
// and cost (never llm.* spans, which mirror them, #283).
export function headerFacts(root: Record<string, any>, spans: NamedAttrs[] = []): HeaderFacts {
  const a = root || {};
  const num = (...keys: string[]) => attrNum(a, ...keys);
  let totalTokens = num("gen_ai.usage.total_tokens", "llm.token_count.total");
  let inputTokens = num("gen_ai.usage.input_tokens", "llm.token_count.prompt");
  let outputTokens = num("gen_ai.usage.output_tokens", "llm.token_count.completion");
  if (totalTokens == null) {
    const t = sumApiSpans(spans, "gen_ai.usage.total_tokens", "llm.token_count.total");
    if (t != null) {
      totalTokens = t;
      inputTokens = inputTokens ?? sumApiSpans(spans, "gen_ai.usage.input_tokens", "llm.token_count.prompt");
      outputTokens = outputTokens ?? sumApiSpans(spans, "gen_ai.usage.output_tokens", "llm.token_count.completion");
    }
  }
  const costUnknown = a["hermes.cost.status"] != null && num("hermes.cost.usage") == null;
  const cost = costUnknown ? null : (num("hermes.cost.usage") ?? sumApiSpans(spans, "hermes.cost.usage"));
  const toolsRaw = a["hermes.turn.tools"];
  let tools: string[] = [];
  if (Array.isArray(toolsRaw)) tools = toolsRaw.map(String);
  else if (typeof toolsRaw === "string" && toolsRaw.trim()) {
    try {
      const parsed = JSON.parse(toolsRaw);
      tools = Array.isArray(parsed)
        ? parsed.map(String)
        : toolsRaw
            .split(",")
            .map((s) => s.trim())
            .filter(Boolean);
    } catch {
      tools = toolsRaw
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean);
    }
  }
  const requestModel = a["gen_ai.request.model"] || a["llm.model_name"] || null;
  const responseModel = a["gen_ai.response.model"] || null;
  return {
    requestModel,
    responseModel: responseModel && responseModel !== requestModel ? responseModel : null,
    inputTokens,
    outputTokens,
    reasoningTokens: num("gen_ai.usage.reasoning.output_tokens", "llm.token_count.completion_details.reasoning"),
    cacheReadTokens: num("gen_ai.usage.cache_read.input_tokens", "gen_ai.usage.cache_read_input_tokens", "llm.token_count.prompt_details.cache_read"),
    totalTokens,
    cost,
    costUnknown,
    tools,
    exitReason: a["hermes.turn.exit_reason"] || null,
    finalStatus: a["hermes.turn.final_status"] || null,
    session: a["hermes.session_id"] || a["session.id"] || a["session_id"] || null,
    turn: num("hermes.turn.number"),
    platform: a["hermes.platform"] || null,
  };
}

/** A Prometheus-style metric name (hermes_tool_duration_sum) as the OTLP name
 *  the plugin emits (hermes.tool.duration). OTLP names pass through (a dotted
 *  ``.count`` can be a real instrument, ``hermes.session.count``; SigNoz's
 *  histogram parts are told apart by the catalogue, see resolveInstrument). */
export function metricOtlpName(n: string): string {
  if (n.includes(".")) return n;
  const base = n.replace(/_(sum|count|bucket|total)$/, "");
  if (base.startsWith("hermes_prompt_cache_")) return "hermes.prompt_cache." + base.slice("hermes_prompt_cache_".length);
  return base.replace(/_/g, ".");
}
