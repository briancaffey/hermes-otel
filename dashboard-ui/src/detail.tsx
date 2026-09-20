// Trace detail pieces (#185): the summary header, per-kind span summaries,
// grouped attributes, and the Spans / Logs / Raw sub-tabs.
import { React, useState, useEffect, fetchJSON, API, Badge, Button, cn } from "./sdk";
import { fmtDurationMs, fmtTokens, fmtAbsTime, kindOf, groupAttrs, headerFacts, parseMessages, prettyJson, HeaderFacts, TreeSpan, KIND_HEX } from "./lib";
import { withBackend } from "./source";
import { navigate } from "./nav";
import { LogLine, LogRec } from "./logs";
import { MiniLabel, ErrorBanner } from "./atoms";

/* eslint-disable @typescript-eslint/no-explicit-any */

export function CopyButton({ text, label }: { text: string; label?: string }) {
  const [done, setDone] = useState(false);
  return (
    <button
      type="button"
      className="otel-link text-[10px] text-muted-foreground"
      title={`copy ${text}`}
      onClick={(e: any) => {
        e.stopPropagation();
        try {
          navigator.clipboard?.writeText(text);
          setDone(true);
          setTimeout(() => setDone(false), 1200);
        } catch {
          /* clipboard unavailable */
        }
      }}
    >
      {done ? "copied" : label || "copy"}
    </button>
  );
}

function Fact({ label, children }: { label: string; children: any }) {
  if (children == null || children === "" || children === false) return null;
  return (
    <div className="min-w-0">
      <div className="text-[10px] uppercase tracking-wide text-muted-foreground">{label}</div>
      <div className="truncate text-sm text-foreground">{children}</div>
    </div>
  );
}

// Header for both sources: the root span's attributes carry the turn totals.
export function TraceHeader({
  title,
  traceId,
  service,
  durationMs,
  rootAttrs,
  spansAttrs,
  error,
  uiUrl,
  uiLabel,
  source,
  onBack,
}: {
  title: string;
  traceId: string;
  service?: string;
  durationMs: number;
  rootAttrs: Record<string, any>;
  spansAttrs?: Record<string, any>[];
  error?: boolean;
  uiUrl?: string | null;
  uiLabel?: string | null;
  source: string;
  onBack: () => void;
}) {
  const f: HeaderFacts = headerFacts(rootAttrs, spansAttrs || []);
  const tokens =
    f.totalTokens != null
      ? `${fmtTokens(f.totalTokens)}${f.inputTokens != null || f.outputTokens != null ? ` (in ${fmtTokens(f.inputTokens ?? 0)} · out ${fmtTokens(f.outputTokens ?? 0)}${f.reasoningTokens ? ` · reasoning ${fmtTokens(f.reasoningTokens)}` : ""}${f.cacheReadTokens ? ` · cache read ${fmtTokens(f.cacheReadTokens)}` : ""})` : ""}`
      : null;
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 space-y-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-lg font-semibold uppercase tracking-tight">{title}</span>
            {error ? <Badge variant="destructive" className="text-[10px]">error</Badge> : null}
            {f.platform ? <Badge variant="secondary" className="text-[10px]">{f.platform}</Badge> : null}
            {f.turn != null ? <Badge variant="secondary" className="text-[10px]">turn {f.turn}</Badge> : null}
          </div>
          <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
            {service ? <span>{service}</span> : null}
            <span className="font-mono">{traceId}</span>
            <CopyButton text={traceId} label="copy id" />
            {uiUrl ? (
              <a className="otel-link" href={uiUrl} target="_blank" rel="noreferrer">
                open in {uiLabel || "backend"} ↗
              </a>
            ) : null}
          </div>
        </div>
        <Button variant="ghost" size="sm" onClick={onBack}>← Back</Button>
      </div>
      <div className="otel-facts-grid">
        <Fact label="duration">{fmtDurationMs(durationMs)}</Fact>
        <Fact label="model">
          {f.requestModel ? <span className="font-mono">{f.requestModel}</span> : null}
          {f.responseModel ? <span className="ml-1 text-xs text-muted-foreground" title="the model named in the response, when it differs from the request">(served: {f.responseModel})</span> : null}
        </Fact>
        <Fact label="tokens">{tokens}</Fact>
        <Fact label="cost">{f.cost != null ? <span className="text-emerald-400">${f.cost.toFixed(4)}</span> : <span className="text-muted-foreground">no pricing data</span>}</Fact>
        <Fact label="tools">{f.tools.length ? f.tools.join(", ") : null}</Fact>
        <Fact label="outcome">{f.finalStatus || f.exitReason ? `${f.finalStatus || ""}${f.finalStatus && f.exitReason ? " · " : ""}${f.exitReason || ""}` : null}</Fact>
        <Fact label="session">
          {f.session ? (
            <button type="button" className="otel-link font-mono" title="show this session's turns" onClick={() => navigate({ tab: "traces", source, view: "sessions", session: String(f.session), trace: "" })}>
              {f.session}
            </button>
          ) : null}
        </Fact>
      </div>
    </div>
  );
}

function Chat({ raw }: { raw: any }) {
  const msgs = parseMessages(raw);
  if (!msgs.length) return null;
  return (
    <div className="space-y-1.5">
      {msgs.map((m, i) => (
        <div key={i} className={cn("otel-msg", `otel-msg-${m.role}`)}>
          <div className="text-[10px] uppercase tracking-wide text-muted-foreground">{m.role}</div>
          <pre className="otel-pre">{m.text}</pre>
        </div>
      ))}
    </div>
  );
}

function Block({ label, children }: { label: string; children: any }) {
  if (children == null) return null;
  return (
    <div className="space-y-1">
      <MiniLabel>{label}</MiniLabel>
      {children}
    </div>
  );
}

// What a person wants first for each kind of span; the attribute table stays
// below, collapsed.
export function SpanSummary({ span }: { span: TreeSpan }) {
  const a = span._attrs || {};
  const kind = kindOf(span.name, a);
  const status = a["hermes.tool.outcome"] || a["status"] || null;
  if (kind === "tool") {
    return (
      <div className="space-y-2">
        <div className="flex flex-wrap items-center gap-2 text-xs">
          {a["tool.name"] ? <Badge variant="secondary" className="font-mono text-[10px]">{String(a["tool.name"])}</Badge> : null}
          {status ? <Badge variant={String(status).toLowerCase().includes("error") || String(status).toLowerCase().includes("fail") ? "destructive" : "secondary"} className="text-[10px]">{String(status)}</Badge> : null}
          {a["hermes.tool.target"] ? <span className="font-mono text-muted-foreground">{String(a["hermes.tool.target"])}</span> : null}
          {a["hermes.tool.decided_by"] ? <span className="text-muted-foreground">decided by {String(a["hermes.tool.decided_by"])}</span> : null}
        </div>
        {a["hermes.tool.command"] ? <Block label="command"><pre className="otel-pre">{String(a["hermes.tool.command"])}</pre></Block> : null}
        {a["input.value"] != null ? <Block label="arguments"><pre className="otel-pre">{prettyJson(a["input.value"])}</pre></Block> : null}
        {a["output.value"] != null ? <Block label="result"><pre className="otel-pre">{prettyJson(a["output.value"])}</pre></Block> : null}
      </div>
    );
  }
  if (kind === "llm" || kind === "api" || kind === "agent") {
    const f = headerFacts(a);
    const output = a["llm.output.content"] ?? a["output.value"];
    const input = a["llm.input_messages"] ?? a["input.value"];
    return (
      <div className="space-y-2">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
          {f.requestModel ? <span className="font-mono text-foreground/80">{f.requestModel}</span> : null}
          {f.responseModel ? <span>served: {f.responseModel}</span> : null}
          {f.totalTokens != null ? <span className="tabular-nums">{fmtTokens(f.totalTokens)} tok{f.inputTokens != null ? ` (in ${fmtTokens(f.inputTokens)} · out ${fmtTokens(f.outputTokens ?? 0)})` : ""}</span> : null}
          {a["llm.response.finish_reason"] ? <span>finish: {String(a["llm.response.finish_reason"])}</span> : null}
          {a["hermes.turn.api_call_count"] != null ? <span>{String(a["hermes.turn.api_call_count"])} API call(s)</span> : null}
        </div>
        {input != null ? <Block label={kind === "agent" ? "user message" : "prompt"}><Chat raw={input} /></Block> : null}
        {output != null ? <Block label="response"><pre className="otel-pre">{prettyJson(output)}</pre></Block> : null}
      </div>
    );
  }
  if (kind === "approval") {
    return (
      <div className="space-y-2">
        <div className="flex flex-wrap items-center gap-2 text-xs">
          {a["hermes.approval.choice"] ? <Badge variant="secondary" className="text-[10px]">👤 {String(a["hermes.approval.choice"])}</Badge> : null}
          {a["hermes.approval.granted"] != null ? <Badge variant={String(a["hermes.approval.granted"]) === "true" ? "secondary" : "destructive"} className="text-[10px]">{String(a["hermes.approval.granted"]) === "true" ? "granted" : "denied"}</Badge> : null}
          {a["hermes.approval.decided_by"] ? <span className="text-muted-foreground">by {String(a["hermes.approval.decided_by"])}</span> : null}
          {a["hermes.approval.duration_ms"] != null ? <span className="text-muted-foreground">waited {fmtDurationMs(Number(a["hermes.approval.duration_ms"]))}</span> : null}
          {String(a["hermes.approval.timed_out"]) === "true" ? <Badge variant="destructive" className="text-[10px]">timed out</Badge> : null}
        </div>
        {a["hermes.approval.command"] ? <Block label="command"><pre className="otel-pre">{String(a["hermes.approval.command"])}</pre></Block> : null}
        {a["hermes.approval.description"] ? <Block label="description"><pre className="otel-pre">{String(a["hermes.approval.description"])}</pre></Block> : null}
      </div>
    );
  }
  return null;
}

export function AttrGroups({ attrs }: { attrs: Record<string, any> }) {
  const groups = groupAttrs(attrs || {});
  if (!groups.length) return <div className="text-xs text-muted-foreground">(no attributes)</div>;
  return (
    <div className="space-y-3">
      {groups.map((g) => (
        <div key={g.prefix}>
          <MiniLabel>{g.prefix}</MiniLabel>
          <dl className="otel-attr-table text-xs">
            {g.entries.map((e) => {
              const v = e.value;
              const rendered = v && typeof v === "object" ? JSON.stringify(v, null, 2) : String(v);
              return (
                <React.Fragment key={e.key}>
                  <dt className="text-muted-foreground" title={e.aliases.length ? `also: ${e.aliases.join(", ")}` : ""}>
                    {e.key}
                    {e.aliases.length ? <span className="ml-1 text-[10px] text-muted-foreground/60">+{e.aliases.length}</span> : null}
                  </dt>
                  <dd className="whitespace-pre-wrap break-words text-foreground">{rendered}</dd>
                </React.Fragment>
              );
            })}
          </dl>
        </div>
      ))}
    </div>
  );
}

// Spans | Logs | Raw under a trace.
export function TraceTabs({
  traceId,
  source,
  logsAvailable,
  spans,
  raw,
}: {
  traceId: string;
  source: string;
  logsAvailable: boolean;
  spans: any;
  raw: any;
}) {
  const [tab, setTab] = useState<"spans" | "logs" | "raw">("spans");
  const [logs, setLogs] = useState<LogRec[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (tab !== "logs" || logs !== null) return;
    const base = source === "live" ? `${API}/live` : API;
    const p = withBackend(new URLSearchParams({ trace_id: traceId, limit: "500", lookback_hours: "8760" }), source);
    fetchJSON(`${base}/logs/search?${p}`)
      .then((r: any) => setLogs(r.logs || []))
      .catch((e: any) => {
        setError(String(e?.message || e));
        setLogs([]);
      });
  }, [tab, logs, source, traceId]);
  const Btn = ({ id, label }: { id: "spans" | "logs" | "raw"; label: string }) => (
    <button type="button" onClick={() => setTab(id)} className={cn("otel-toggle px-3 py-1 text-xs font-medium transition-colors", tab === id ? "otel-toggle-active text-foreground" : "text-muted-foreground hover:text-foreground")}>
      {label}
    </button>
  );
  return (
    <div className="space-y-3">
      <div className="otel-card-bg inline-flex border border-border p-0.5">
        <Btn id="spans" label="Spans" />
        {logsAvailable ? <Btn id="logs" label="Logs" /> : null}
        <Btn id="raw" label="Raw" />
      </div>
      {tab === "spans" ? spans : null}
      {tab === "logs" ? (
        error ? (
          <ErrorBanner error={error} />
        ) : logs === null ? (
          <div className="text-xs text-muted-foreground">Loading…</div>
        ) : logs.length === 0 ? (
          <div className="border border-dashed border-border px-4 py-6 text-center text-xs text-muted-foreground">No log lines carry this trace id.</div>
        ) : (
          <div className="otel-card-bg overflow-hidden border border-border font-mono text-xs">
            {logs.map((l, i) => (
              <LogLine key={l.seq ?? i} l={l} absolute />
            ))}
          </div>
        )
      ) : null}
      {tab === "raw" ? <pre className="otel-pre otel-raw">{JSON.stringify(raw, null, 2)}</pre> : null}
    </div>
  );
}

export const kindColor = (name: string, attrs?: Record<string, any>) => KIND_HEX[kindOf(name, attrs)];
export { fmtAbsTime };
