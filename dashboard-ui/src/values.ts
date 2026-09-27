// Pure helpers behind the structured attribute views (tested in
// test/values.test.ts): what an attribute value *is* (a conversation, a list
// of tool calls, a JSON object, prose, a command, a list, a number...) and
// the per-key display rules the trace detail applies.

/* eslint-disable @typescript-eslint/no-explicit-any */

export type MessagePart =
  | { type: "text"; text: string }
  | { type: "tool_call"; id: string | null; name: string; args: any }
  | { type: "other"; label: string; value: any };

export type Message = {
  role: string; // system | user | assistant | tool | developer | ...
  parts: MessagePart[];
  toolCallId: string | null; // for role=tool: the call this result answers
  name: string | null; // tool name on a tool result, when given
};

export type ToolCall = { id: string | null; name: string; args: any };

export type ValueKind =
  | "messages"
  | "tool_calls"
  | "json"
  | "markdown"
  | "text"
  | "code"
  | "list"
  | "command"
  | "path"
  | "url"
  | "duration"
  | "count"
  | "bool"
  | "id"
  | "empty";

export type Classified = { kind: ValueKind; value: any; text: string };

/** Parse a JSON string; anything else comes back unchanged (objects pass through). */
export function parseJsonish(raw: any): any {
  if (typeof raw !== "string") return raw;
  const t = raw.trim();
  if (!(t.startsWith("[") || t.startsWith("{"))) return raw;
  try {
    return JSON.parse(t);
  } catch {
    return raw;
  }
}

/** Python list/dict repr the plugin sometimes leaves as text ("['tool_calls']"). */
export function parsePyRepr(raw: any): any {
  if (typeof raw !== "string") return raw;
  const t = raw.trim();
  if (!((t.startsWith("[") && t.endsWith("]")) || (t.startsWith("{") && t.endsWith("}")))) return raw;
  if (!t.includes("'")) return raw;
  try {
    return JSON.parse(t.replace(/'/g, '"').replace(/\bTrue\b/g, "true").replace(/\bFalse\b/g, "false").replace(/\bNone\b/g, "null"));
  } catch {
    return raw;
  }
}

function toolCallOf(tc: any): ToolCall | null {
  if (!tc || typeof tc !== "object") return null;
  const fn = tc.function && typeof tc.function === "object" ? tc.function : tc;
  const name = fn.name ?? tc.name;
  if (typeof name !== "string" || !name) return null;
  let args = fn.arguments ?? tc.arguments ?? tc.args ?? tc.input ?? null;
  if (typeof args === "string") args = parseJsonish(args);
  return { id: tc.id != null ? String(tc.id) : null, name, args };
}

/** A list of tool calls, in either the OpenAI or the flattened Hermes shape. */
export function parseToolCalls(raw: any): ToolCall[] | null {
  const v = parseJsonish(raw);
  if (!Array.isArray(v) || !v.length) return null;
  const calls = v.map(toolCallOf);
  return calls.every((c) => c !== null) ? (calls as ToolCall[]) : null;
}

function partsOfContent(c: any): MessagePart[] {
  if (c == null || c === "") return [];
  if (typeof c === "string") return [{ type: "text", text: c }];
  if (Array.isArray(c)) {
    const out: MessagePart[] = [];
    for (const p of c) {
      if (typeof p === "string") out.push({ type: "text", text: p });
      else if (p && typeof p === "object" && typeof p.text === "string") out.push({ type: "text", text: p.text });
      else if (p && typeof p === "object" && (p.type === "tool_use" || p.type === "tool_call")) {
        const tc = toolCallOf(p);
        if (tc) out.push({ type: "tool_call", ...tc });
      } else out.push({ type: "other", label: (p && p.type) || "part", value: p });
    }
    return out;
  }
  return [{ type: "other", label: "content", value: c }];
}

/**
 * Chat messages (OpenAI-style, OpenInference `message.*`, or the Hermes
 * flattened form) → a list the view renders as a conversation. `null` when
 * the value is not a message list.
 */
export function parseChat(raw: any): Message[] | null {
  const v = parseJsonish(raw);
  const list = Array.isArray(v) ? v : v && typeof v === "object" && (v.role || v["message.role"]) ? [v] : null;
  if (!list || !list.length) return null;
  const out: Message[] = [];
  for (const m of list) {
    if (!m || typeof m !== "object") return null;
    const role = m.role ?? m["message.role"];
    if (typeof role !== "string") return null;
    const parts = partsOfContent(m.content ?? m["message.content"]);
    const tcs = m.tool_calls ?? m["message.tool_calls"];
    if (Array.isArray(tcs)) {
      for (const tc of tcs) {
        const call = toolCallOf(tc);
        if (call) parts.push({ type: "tool_call", ...call });
      }
    }
    out.push({
      role,
      parts,
      toolCallId: m.tool_call_id != null ? String(m.tool_call_id) : null,
      name: typeof m.name === "string" ? m.name : null,
    });
  }
  return out;
}

const MD_CUES = /(^|\n)(#{1,6} |\s*[-*] |\s*\d+\. |```|> )|\*\*[^*]+\*\*|`[^`]+`|\[[^\]]+\]\([^)]+\)/;

/** JSON that did not parse (a truncated preview) or other code-like text: keep it monospace. */
export function looksLikeCode(text: string): boolean {
  const t = text.trim();
  return /^[[{]/.test(t) || /\\n.*\\n/.test(t);
}

/** Prose that carries markdown structure (headings, lists, fences, emphasis, links). */
export function looksLikeMarkdown(text: string): boolean {
  return typeof text === "string" && text.length > 0 && MD_CUES.test(text);
}

const COMMAND_KEYS = new Set(["hermes.tool.command", "hermes.approval.command", "hermes.turn.tool_commands"]);
const PATH_KEYS = new Set(["hermes.skill.path", "hermes.tool.target", "hermes.turn.tool_targets", "code.file.path"]);
const ID_KEYS = new Set([
  "hermes.session_id",
  "session.id",
  "session_id",
  "gen_ai.conversation.id",
  "wandb.thread_id",
  "gen_ai.tool.call.id",
  "gen_ai.response.id",
  "hermes.session.previous_id",
  "hermes.subagent.child_session_id",
  "hermes.subagent.child_id",
  "hermes.subagent.parent_session_id",
  "hermes.subagent.parent_turn_id",
  "hermes.subagent.parent_id",
  "hermes.cron.job_id",
  "correlation.id",
]);
const PROSE_KEYS = new Set([
  "gen_ai.system_instructions",
  "hermes.subagent.goal",
  "hermes.subagent.summary",
  "hermes.approval.description",
  "llm.output.content",
  "error.message",
]);
// Keys whose value is a delimited list: the separator the plugin uses.
const LIST_KEYS: Record<string, string> = {
  "hermes.turn.tools": ",",
  "hermes.turn.tool_outcomes": ",",
  "hermes.turn.tool_targets": ",",
  "hermes.turn.skills": ",",
  "hermes.turn.tool_commands": "|",
  "hermes.approval.pattern_keys": ",",
  "gen_ai.request.stop_sequences": ",",
};

/** Split one of the plugin's delimited list attributes. */
export function splitList(key: string, value: any): string[] | null {
  if (typeof value !== "string") return Array.isArray(value) ? value.map(String) : null;
  const sep = LIST_KEYS[key];
  const py = parsePyRepr(value);
  if (Array.isArray(py)) return py.map(String);
  if (!sep) return null;
  return value
    .split(sep)
    .map((s) => s.trim())
    .filter(Boolean);
}

/** What an attribute value is, given its key; drives the per-key rendering. */
export function classify(key: string, raw: any): Classified {
  if (raw == null || raw === "") return { kind: "empty", value: raw, text: "" };
  const text = typeof raw === "string" ? raw : JSON.stringify(raw);
  const k = key.toLowerCase();
  if (typeof raw === "boolean" || /^(true|false)$/i.test(text.trim())) {
    return { kind: "bool", value: /^true$/i.test(text.trim()) || raw === true, text };
  }
  if (COMMAND_KEYS.has(key) && key !== "hermes.turn.tool_commands") return { kind: "command", value: text, text };
  const list = splitList(key, raw);
  const isRepr = typeof raw === "string" && Array.isArray(parsePyRepr(raw));
  if (list && (list.length > 1 || key in LIST_KEYS || isRepr)) return { kind: "list", value: list, text };
  if (ID_KEYS.has(key)) return { kind: "id", value: text, text };
  if (PATH_KEYS.has(key)) return { kind: "path", value: text, text };
  if (/^https?:\/\/\S+$/.test(text.trim())) return { kind: "url", value: text.trim(), text };
  const num = typeof raw === "number" ? raw : /^-?\d+(\.\d+)?$/.test(text.trim()) ? Number(text) : null;
  if (num != null && Number.isFinite(num)) {
    if (/(_ms|\.ms|duration_ms|latency_ms)$/.test(k)) return { kind: "duration", value: num, text };
    if (/duration_s$/.test(k)) return { kind: "duration", value: num * 1000, text };
    return { kind: "count", value: num, text };
  }
  const chat = parseChat(raw);
  if (chat) return { kind: "messages", value: chat, text };
  const calls = parseToolCalls(raw);
  if (calls) return { kind: "tool_calls", value: calls, text };
  const parsed = parseJsonish(raw);
  if (parsed && typeof parsed === "object") return { kind: "json", value: parsed, text };
  if (looksLikeCode(text)) return { kind: "code", value: text, text };
  if (PROSE_KEYS.has(key) || looksLikeMarkdown(text)) return { kind: "markdown", value: text, text };
  return { kind: "text", value: text, text };
}

/** Pretty JSON for the raw view; text stays text. */
export function pretty(raw: any): string {
  if (raw == null) return "";
  const v = parseJsonish(raw);
  return typeof v === "string" ? v : JSON.stringify(v, null, 2);
}

/** `1234567` → `1,234,567`. */
export function fmtCount(n: number): string {
  return Number.isInteger(n) ? n.toLocaleString("en-US") : String(n);
}

/** A tool result: `{output: "...", ...rest}` → the output text and the other fields. */
export function splitToolResult(raw: any): { output: string | null; rest: Record<string, any> | null; error: string | null } {
  const v = parseJsonish(raw);
  if (!v || typeof v !== "object" || Array.isArray(v)) return { output: typeof v === "string" ? v : null, rest: null, error: null };
  const out: any = v.output ?? v.result ?? v.content ?? v.stdout ?? null;
  const rest: Record<string, any> = {};
  for (const [k, val] of Object.entries(v)) {
    if (k === "output" || k === "result" || k === "content" || k === "stdout") continue;
    rest[k] = val;
  }
  const err = typeof v.error === "string" ? v.error : v.success === false ? "failed" : null;
  return { output: typeof out === "string" ? out : out == null ? null : JSON.stringify(out, null, 2), rest: Object.keys(rest).length ? rest : null, error: err };
}

/** The turn's tool calls: `hermes.turn.tools` holds the distinct tool names while
 *  the commands, outcomes and targets are per call, so the rows follow the longest
 *  list and a single tool name repeats. */
export function turnTools(a: Record<string, any>): { tool: string | null; outcome: string | null; command: string | null; target: string | null }[] {
  const tools = splitList("hermes.turn.tools", a["hermes.turn.tools"]) || [];
  const outcomes = splitList("hermes.turn.tool_outcomes", a["hermes.turn.tool_outcomes"]) || [];
  const commands = splitList("hermes.turn.tool_commands", a["hermes.turn.tool_commands"]) || [];
  const targets = splitList("hermes.turn.tool_targets", a["hermes.turn.tool_targets"]) || [];
  const count = Number(a["hermes.turn.tool_count"]) || 0;
  const rows = Math.max(tools.length, outcomes.length, commands.length, targets.length, count);
  const out = [];
  for (let i = 0; i < rows; i++) {
    out.push({
      tool: tools[i] ?? (tools.length === 1 ? tools[0] : null),
      outcome: outcomes[i] ?? (outcomes.length === 1 ? outcomes[0] : null),
      command: commands[i] ?? null,
      target: targets[i] ?? null,
    });
  }
  return out;
}

export type ViewMode = "structured" | "raw";
const MODE_KEY = "hermes_otel.attr_view";

export function readViewMode(): ViewMode {
  try {
    return localStorage.getItem(MODE_KEY) === "raw" ? "raw" : "structured";
  } catch {
    return "structured";
  }
}

export function writeViewMode(m: ViewMode): void {
  try {
    localStorage.setItem(MODE_KEY, m);
  } catch {
    /* storage unavailable */
  }
}
