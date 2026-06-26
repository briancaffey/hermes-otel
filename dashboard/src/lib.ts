// Shared helpers: formatting, span-kind classification, attribute access.

export type LiveSpan = {
  trace_id: string;
  span_id: string;
  parent_span_id: string | null;
  name: string;
  start_time_unix_nano: number;
  end_time_unix_nano: number;
  duration_ms: number | null;
  status: string; // OK / ERROR / UNSET
  attributes: Record<string, any>;
  seq: number;
};

export type LiveMetric = {
  name: string;
  value: number;
  attributes: Record<string, any>;
  time_unix_nano: number;
  seq: number;
};

// ── span-kind classification (drives colour + grouping) ──────────────────
export type Kind =
  | "agent"
  | "llm"
  | "api"
  | "tool"
  | "skill"
  | "approval"
  | "subagent"
  | "session"
  | "other";

export function spanKind(s: { name: string; attributes?: Record<string, any> }): Kind {
  const a = s.attributes || {};
  const hk = a["hermes.span_kind"];
  if (hk === "skill") return "skill";
  if (hk === "approval") return "approval";
  const n = (s.name || "").toLowerCase();
  if (n === "agent" || n === "cron") return "agent";
  if (n.startsWith("session")) return "session";
  if (n.startsWith("skill.")) return "skill";
  if (n.startsWith("approval")) return "approval";
  if (n.startsWith("subagent")) return "subagent";
  if (n.startsWith("llm.")) return "llm";
  if (n.startsWith("api.")) return "api";
  if (n.startsWith("tool.")) return "tool";
  return "other";
}

// Theme-friendly accent per kind (CSS custom props live in dist/style.css).
export const KIND_COLOR: Record<Kind, string> = {
  agent: "var(--otel-agent)",
  llm: "var(--otel-llm)",
  api: "var(--otel-api)",
  tool: "var(--otel-tool)",
  skill: "var(--otel-skill)",
  approval: "var(--otel-approval)",
  subagent: "var(--otel-subagent)",
  session: "var(--otel-agent)",
  other: "var(--otel-other)",
};

export const KIND_LABEL: Record<Kind, string> = {
  agent: "agent",
  llm: "llm",
  api: "api",
  tool: "tool",
  skill: "skill",
  approval: "approval",
  subagent: "subagent",
  session: "session",
  other: "span",
};

// ── attribute readers (handle the dual conventions we emit) ──────────────
export function attrNum(a: Record<string, any>, ...keys: string[]): number | null {
  for (const k of keys) {
    const v = a[k];
    if (typeof v === "number") return v;
    if (typeof v === "string" && v.trim() && !isNaN(Number(v))) return Number(v);
  }
  return null;
}

export function spanTokens(s: LiveSpan): number | null {
  return attrNum(s.attributes, "gen_ai.usage.total_tokens", "llm.token_count.total");
}
export function spanCost(s: LiveSpan): number | null {
  return attrNum(s.attributes, "hermes.cost.usage");
}
export function spanModel(s: LiveSpan): string | null {
  const a = s.attributes;
  return a["gen_ai.response.model"] || a["gen_ai.request.model"] || a["llm.model_name"] || null;
}

// ── formatting ───────────────────────────────────────────────────────────
export function fmtDuration(ms: number | null | undefined): string {
  if (ms == null) return "—";
  if (ms < 1) return `${(ms * 1000).toFixed(0)}µs`;
  if (ms < 1000) return `${ms.toFixed(ms < 10 ? 1 : 0)}ms`;
  if (ms < 60000) return `${(ms / 1000).toFixed(2)}s`;
  const m = Math.floor(ms / 60000);
  return `${m}m ${Math.round((ms % 60000) / 1000)}s`;
}
export function fmtCost(usd: number | null | undefined): string {
  if (usd == null) return "$0";
  if (usd === 0) return "$0";
  if (usd < 0.01) return `$${usd.toFixed(4)}`;
  return `$${usd.toFixed(2)}`;
}
export function fmtInt(n: number | null | undefined): string {
  if (n == null) return "0";
  return n.toLocaleString();
}
export function fmtAgo(unixNano: number): string {
  const ms = unixNano / 1e6;
  const diff = Date.now() - ms;
  if (diff < 1500) return "now";
  if (diff < 60000) return `${Math.round(diff / 1000)}s ago`;
  if (diff < 3600000) return `${Math.round(diff / 60000)}m ago`;
  return `${Math.round(diff / 3600000)}h ago`;
}

export function sessionOf(s: LiveSpan): string | null {
  const a = s.attributes;
  return a["hermes.session_id"] || a["session_id"] || a["session.id"] || null;
}
