// Structured views of span attributes: a conversation with roles, tool calls,
// JSON as key/value rows, prose as markdown, lists as chips, and a
// structured / raw toggle on every rich value (the choice is remembered per
// browser).
import { React, useState, useContext, createContext, Badge, cn } from "./sdk";
import { fmtDurationMs } from "./lib";
import { Markdown } from "./markdown";
import {
  classify,
  fmtCount,
  looksLikeCode,
  looksLikeMarkdown,
  Message,
  parseJsonish,
  pretty,
  readViewMode,
  splitToolResult,
  ToolCall,
  ViewMode,
  writeViewMode,
  splitToolResult,
} from "./values";

const CLAMP_CHARS = 1600;
/** Above this a value is offered for copying rather than rendered in full (#283). */
const HUGE_CHARS = 200_000;

// One structured / raw preference for the whole tab, held by a context the
// shell provides (#283); every block follows the switch and the choice is
// remembered per browser.
type ModeCtx = [ViewMode, (m: ViewMode) => void];
const ViewModeContext: any = createContext ? createContext<ModeCtx | null>(null) : null;

export function ViewModeProvider({ children }: { children: any }) {
  const [mode, setMode] = useState<ViewMode>(readViewMode());
  const change = (m: ViewMode) => {
    writeViewMode(m);
    setMode(m);
  };
  if (!ViewModeContext) return children;
  return React.createElement(ViewModeContext.Provider, { value: [mode, change] }, children);
}

function useViewMode(): ModeCtx {
  const ctx = ViewModeContext && useContext ? (useContext(ViewModeContext) as ModeCtx | null) : null;
  const own = useState<ViewMode>(readViewMode());
  if (ctx) return ctx;
  return [
    own[0],
    (m: ViewMode) => {
      writeViewMode(m);
      own[1](m);
    },
  ];
}

/** Long text collapsed to a preview with a "show all" control. */
export function LongText({ text, mono, markdown }: { text: string; mono?: boolean; markdown?: boolean }) {
  const [open, setOpen] = useState(false);
  const long = text.length > CLAMP_CHARS;
  const huge = text.length > HUGE_CHARS;
  const shown = long && !open ? text.slice(0, CLAMP_CHARS) : text;
  return (
    <div className="otel-longtext">
      {markdown ? <Markdown text={shown} /> : <pre className={cn("otel-pre", mono ? "" : "otel-prose")}>{shown}</pre>}
      {long ? (
        <span className="otel-more inline-flex items-center gap-2">
          <button type="button" className="otel-link" onClick={() => setOpen((o) => !o)}>
            {open ? "show less" : `show all (${fmtCount(text.length)} chars${huge ? ", large" : ""})`}
          </button>
        </span>
      ) : null}
    </div>
  );
}

function Scalar({ v }: { v: any }) {
  const mono = typeof v !== "string";
  return <span className={mono ? "font-mono" : ""}>{String(v)}</span>;
}

/** A JSON object as key / value rows; nested objects stay pretty JSON. */
export function KvTable({ value }: { value: Record<string, any> }) {
  const entries = Object.entries(value || {});
  if (!entries.length) return <span className="otel-unset">empty</span>;
  return (
    <dl className="otel-attr-table otel-kv-table text-xs">
      {entries.map(([k, v]) => (
        <React.Fragment key={k}>
          <dt className="text-muted-foreground">{k}</dt>
          <dd className="min-w-0 break-words">
            {v && typeof v === "object" ? (
              <pre className="otel-pre otel-pre-inline">{JSON.stringify(v, null, 2)}</pre>
            ) : typeof v === "string" && v.includes("\n") ? (
              <LongText text={v} mono />
            ) : (
              <Scalar v={v} />
            )}
          </dd>
        </React.Fragment>
      ))}
    </dl>
  );
}

/** Strings as chips (a turn's tools, outcomes, finish reasons). */
export function Chips({ items, mono }: { items: string[]; mono?: boolean }) {
  return (
    <span className="otel-chips">
      {items.map((s, i) => (
        <span key={i} className={cn("otel-chip", mono ? "font-mono" : "")}>
          {s}
        </span>
      ))}
    </span>
  );
}

function ToolCallCard({ call }: { call: ToolCall }) {
  const args = call.args;
  return (
    <div className="otel-toolcall">
      <div className="otel-toolcall-head">
        <span className="otel-chip otel-chip-tool font-mono">{call.name}</span>
        {call.id ? (
          <span className="font-mono text-[10px] text-muted-foreground" title="tool call id">
            {call.id}
          </span>
        ) : null}
      </div>
      {args && typeof args === "object" && !Array.isArray(args) ? (
        <KvTable value={args} />
      ) : args != null ? (
        <pre className="otel-pre otel-pre-inline">{typeof args === "string" ? args : JSON.stringify(args, null, 2)}</pre>
      ) : null}
    </div>
  );
}

/** A list of tool calls (an assistant turn that only called tools). */
export function ToolCalls({ calls }: { calls: ToolCall[] }) {
  return (
    <div className="otel-toolcalls">
      {calls.map((c, i) => (
        <ToolCallCard key={c.id || i} call={c} />
      ))}
    </div>
  );
}

const ROLE_LABEL: Record<string, string> = { system: "system", user: "user", assistant: "assistant", tool: "tool result", developer: "developer" };

/** Messages as a conversation: role, then each part (prose as markdown, tool calls as cards). */
export function Chat({ messages }: { messages: Message[] }) {
  const [openSystem, setOpenSystem] = useState(false);
  return (
    <div className="otel-chat">
      {messages.map((m, i) => {
        const role = m.role.toLowerCase();
        const text = m.parts
          .filter((p) => p.type === "text")
          .map((p: any) => p.text)
          .join("\n");
        const isSystem = role === "system" || role === "developer";
        const collapsed = isSystem && !openSystem && text.length > 400;
        return (
          <div key={i} className={cn("otel-msg", `otel-msg-${role}`)}>
            <div className="otel-msg-head">
              <span className={cn("otel-role", `otel-role-${role}`)}>{ROLE_LABEL[role] || role}</span>
              {m.name ? <span className="font-mono text-[10px] text-muted-foreground">{m.name}</span> : null}
              {m.toolCallId ? (
                <span className="font-mono text-[10px] text-muted-foreground" title="answers this tool call">
                  ↳ {m.toolCallId}
                </span>
              ) : null}
              {collapsed ? (
                <button type="button" className="otel-link text-[10px]" onClick={() => setOpenSystem(true)}>
                  show all ({fmtCount(text.length)} chars)
                </button>
              ) : null}
            </div>
            {m.parts.map((p, j) => {
              if (p.type === "text") {
                const body = collapsed ? p.text.slice(0, 400) + " …" : p.text;
                if (role === "tool") return <ToolResultBody key={j} raw={body} />;
                return looksLikeMarkdown(body) || role === "assistant" ? (
                  <Markdown key={j} text={body} />
                ) : (
                  <pre key={j} className="otel-pre otel-prose">
                    {body}
                  </pre>
                );
              }
              if (p.type === "tool_call") return <ToolCallCard key={j} call={p} />;
              return (
                <div key={j} className="text-[11px] text-muted-foreground">
                  <span className="otel-chip">{p.label}</span>
                  <pre className="otel-pre otel-pre-inline">{JSON.stringify(p.value, null, 2)}</pre>
                </div>
              );
            })}
          </div>
        );
      })}
    </div>
  );
}

/** A tool's result: JSON `{output, ...}` shows the output as text and the rest as rows. */
export function ToolResultBody({ raw }: { raw: any }) {
  const v = parseJsonish(raw);
  if (Array.isArray(v)) return <pre className="otel-pre">{JSON.stringify(v, null, 2)}</pre>;
  const { output, rest } = splitToolResult(raw);
  if (v && typeof v === "object") {
    return (
      <div className="otel-toolresult">
        {output != null ? <LongText text={output} mono={!looksLikeMarkdown(output)} markdown={looksLikeMarkdown(output)} /> : null}
        {rest ? <KvTable value={rest} /> : null}
      </div>
    );
  }
  const text = String(v ?? "");
  const md = looksLikeMarkdown(text) && !looksLikeCode(text);
  return <LongText text={text} mono={!md} markdown={md} />;
}

/** The structured / raw switch shown next to a rich value. */
function ModeToggle({ mode, onChange }: { mode: ViewMode; onChange: (m: ViewMode) => void }) {
  const Btn = ({ id, label }: { id: ViewMode; label: string }) => (
    <button
      type="button"
      aria-pressed={mode === id}
      onClick={() => onChange(id)}
      className={cn("otel-toggle otel-mode-btn", mode === id ? "otel-toggle-active text-foreground" : "text-muted-foreground hover:text-foreground")}
    >
      {label}
    </button>
  );
  return (
    <span className="otel-mode" role="group" aria-label="structured or raw">
      <Btn id="structured" label="structured" />
      <Btn id="raw" label="raw" />
    </span>
  );
}

/** One attribute value, rendered by what it is; rich values get the toggle. */
export function ValueView({ attrKey, value, label, onSessionClick }: { attrKey: string; value: any; label?: string; onSessionClick?: (id: string) => void }) {
  const [mode, change] = useViewMode();
  const c = classify(attrKey, value);
  const rich = c.kind === "messages" || c.kind === "tool_calls" || c.kind === "json" || c.kind === "markdown";
  const head =
    label || rich ? (
      <div className="otel-value-head">
        {label ? <span className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">{label}</span> : null}
        {rich ? <ModeToggle mode={mode} onChange={change} /> : null}
      </div>
    ) : null;
  let body: any;
  if (rich && mode === "raw") body = <LongText text={pretty(value)} mono />;
  else
    switch (c.kind) {
      case "empty":
        body = <span className="otel-unset">unset</span>;
        break;
      case "messages":
        body = <Chat messages={c.value} />;
        break;
      case "tool_calls":
        body = <ToolCalls calls={c.value} />;
        break;
      case "json":
        body = Array.isArray(c.value) ? <pre className="otel-pre">{JSON.stringify(c.value, null, 2)}</pre> : <KvTable value={c.value} />;
        break;
      case "markdown":
        body = <LongText text={c.value} markdown />;
        break;
      case "code":
        body = <LongText text={c.value} mono />;
        break;
      case "command":
        body = <pre className="otel-pre otel-cmd">$ {c.value}</pre>;
        break;
      case "list":
        body = <Chips items={c.value} mono={attrKey.includes("command") || attrKey.includes("target")} />;
        break;
      case "path":
        body = <span className="font-mono break-all">{c.value}</span>;
        break;
      case "url":
        body = (
          <a className="otel-link font-mono break-all" href={c.value} target="_blank" rel="noreferrer noopener">
            {c.value}
          </a>
        );
        break;
      case "duration":
        body = <span className="tabular-nums">{fmtDurationMs(c.value)}</span>;
        break;
      case "count":
        body = <span className="tabular-nums">{fmtCount(c.value)}</span>;
        break;
      case "bool":
        body = <span className={cn("otel-chip", c.value ? "otel-chip-yes" : "otel-chip-no")}>{c.value ? "yes" : "no"}</span>;
        break;
      case "id":
        body =
          onSessionClick && /session|conversation|thread/.test(attrKey) ? (
            <button type="button" className="otel-link font-mono" title="show this session's turns" onClick={() => onSessionClick(c.value)}>
              {c.value}
            </button>
          ) : (
            <span className="font-mono break-all">{c.value}</span>
          );
        break;
      default:
        body = c.text.length > 200 || c.text.includes("\n") ? <LongText text={c.text} /> : <span className="break-words">{c.text}</span>;
    }
  const errorish = attrKey === "error.message";
  return (
    <div className={cn("otel-value", errorish ? "otel-value-error" : "")}>
      {head}
      {body}
    </div>
  );
}

/** A small labelled badge row used by the span summaries. */
export function Facts({ items }: { items: { label: string; value: any; mono?: boolean; tone?: "ok" | "bad" | "warn" }[] }) {
  const shown = items.filter((f) => f.value != null && f.value !== "" && f.value !== false);
  if (!shown.length) return null;
  return (
    <div className="otel-factrow">
      {shown.map((f, i) => (
        <span key={i} className="otel-fact">
          <span className="otel-fact-label">{f.label}</span>
          <span className={cn("otel-fact-value", f.mono ? "font-mono" : "", f.tone ? `otel-tone-${f.tone}` : "")}>{String(f.value)}</span>
        </span>
      ))}
    </div>
  );
}

export function StatusBadge({ value }: { value: any }) {
  if (value == null || value === "") return null;
  const s = String(value).toLowerCase();
  const bad = /error|fail|denied|timed?_?out|cancel/.test(s);
  return (
    <Badge variant={bad ? "destructive" : "secondary"} className="text-[10px]">
      {String(value)}
    </Badge>
  );
}
