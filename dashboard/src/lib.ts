// Shared helpers — ported in full from the original dashboard + live-mode adds.
// Trace (backend) data is OTLP/Tempo-shaped; live data is the compact dict the
// in-process store emits. Both flow through the same rendering.

/* eslint-disable @typescript-eslint/no-explicit-any */

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
export function fmtAbsTime(unixNano: number): string {
  if (!unixNano) return "";
  try {
    return new Date(unixNano / 1e6).toLocaleString();
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
  return num.toLocaleString();
}
export function fmtCost(usd: number | null | undefined): string {
  if (!usd) return "$0";
  if (usd < 0.01) return `$${usd.toFixed(4)}`;
  return `$${usd.toFixed(2)}`;
}
export function fmtInt(n: number | null | undefined): string {
  return n == null ? "0" : n.toLocaleString();
}
export function clip(s: any, max: number): string | null {
  if (s == null) return null;
  let str = (typeof s === "string" ? s : String(s)).replace(/\s+/g, " ").trim();
  if (!str) return null;
  return str.length <= max ? str : str.slice(0, max - 1) + "…";
}

// ── span-kind classification (drives icon + accent) ──────────────────────
export type Kind =
  | "agent"
  | "llm"
  | "api"
  | "tool"
  | "skill"
  | "approval"
  | "subagent"
  | "session"
  | "cron"
  | "other";

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

// Tailwind text-color class per kind (host palette → matches the theme).
export const KIND_TEXT: Record<Kind, string> = {
  agent: "text-emerald-400",
  llm: "text-sky-400",
  api: "text-cyan-400",
  tool: "text-amber-400",
  skill: "text-emerald-300",
  approval: "text-pink-400",
  subagent: "text-violet-400",
  session: "text-emerald-400",
  cron: "text-violet-400",
  other: "text-muted-foreground",
};
// Bar fill (currentColor via the text class won't reach SVG fill cleanly, so a
// CSS-var map is used for waterfall/stream accents). Values reference the same
// hues but are concrete so they render inside <svg>/inline style.
export const KIND_HEX: Record<Kind, string> = {
  agent: "#34d399",
  llm: "#38bdf8",
  api: "#22d3ee",
  tool: "#fbbf24",
  skill: "#6ee7b7",
  approval: "#f472b6",
  subagent: "#a78bfa",
  session: "#34d399",
  cron: "#a78bfa",
  other: "#94a3b8",
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
export function traceSpanCount(trace: any): number | null {
  if (trace.serviceStats) {
    let total = 0;
    for (const k in trace.serviceStats) total += trace.serviceStats[k].spanCount || 0;
    if (total) return total;
  }
  const ss = trace.spanSets || (trace.spanSet ? [trace.spanSet] : []);
  if (ss.length && ss[0].spans) return ss[0].spans.length;
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
export const liveTokens = (s: LiveSpan) =>
  attrNum(s.attributes, "gen_ai.usage.total_tokens", "llm.token_count.total");
export const liveCost = (s: LiveSpan) => attrNum(s.attributes, "hermes.cost.usage");
export const liveModel = (s: LiveSpan) =>
  s.attributes["gen_ai.response.model"] ||
  s.attributes["gen_ai.request.model"] ||
  s.attributes["llm.model_name"] ||
  null;
export const sessionOf = (s: LiveSpan) =>
  s.attributes["hermes.session_id"] || s.attributes["session_id"] || s.attributes["session.id"] || null;
