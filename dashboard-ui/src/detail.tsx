// Trace detail pieces (#185, #283): the summary header, per-kind span
// summaries, grouped attributes, and the Spans / Logs / Raw sub-tabs.
import { React, useState, useEffect, useMemo, Badge, Button, cn } from "./sdk";
import { fmtDurationMs, fmtTokens, fmtCostExact, kindOf, groupAttrs, headerFacts, HeaderFacts, TreeSpan, NamedAttrs, KIND_COLOR } from "./lib";
import { ValueView, Facts, StatusBadge, Chips } from "./render";
import { splitList, turnTools, fmtCount } from "./values";
import { withBackend } from "./source";
import { navigate } from "./nav";
import { api } from "./sdk";
import { LogRec, LogRow } from "./logs";
import { MiniLabel, ErrorBanner, CopyButton, Segmented } from "./atoms";

export { CopyButton };

function Fact({ label, children }: { label: string; children: any }) {
  if (children == null || children === "" || children === false) return null;
  return (
    <div className="min-w-0">
      <div className="text-[10px] uppercase tracking-wide text-muted-foreground">{label}</div>
      <div className="truncate text-sm text-foreground">{children}</div>
    </div>
  );
}

// Header for both sources: the root span's attributes carry the turn totals,
// with the api.* spans as the one fallback (lib.headerFacts).
export function TraceHeader({
  title,
  traceId,
  service,
  durationMs,
  rootAttrs,
  spans,
  error,
  partial,
  truncated,
  spanCount,
  uiUrl,
  uiLabel,
  source,
  onBack,
}: {
  title: string;
  traceId: string;
  service?: string | null;
  durationMs: number;
  rootAttrs: Record<string, any>;
  spans?: NamedAttrs[];
  error?: boolean;
  partial?: boolean;
  truncated?: boolean;
  spanCount?: number | null;
  uiUrl?: string | null;
  uiLabel?: string | null;
  source: string;
  onBack: () => void;
}) {
  const f: HeaderFacts = headerFacts(rootAttrs, spans || []);
  const tokens =
    f.totalTokens != null
      ? `${fmtTokens(f.totalTokens)}${
          f.inputTokens != null || f.outputTokens != null
            ? ` (in ${fmtTokens(f.inputTokens ?? 0)} · out ${fmtTokens(f.outputTokens ?? 0)}${f.reasoningTokens ? ` · reasoning ${fmtTokens(f.reasoningTokens)}` : ""}${f.cacheReadTokens ? ` · cache read ${fmtTokens(f.cacheReadTokens)}` : ""})`
            : ""
        }`
      : null;
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 space-y-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-lg font-semibold uppercase tracking-tight">{title}</span>
            {error ? (
              <Badge variant="destructive" className="text-[10px]">
                error
              </Badge>
            ) : null}
            {partial ? (
              <Badge variant="secondary" className="text-[10px]" title="the root span has not finished yet; totals are provisional">
                in progress
              </Badge>
            ) : null}
            {truncated ? (
              <Badge variant="secondary" className="text-[10px]" title={`the backend returned the first spans only${spanCount ? ` of ${spanCount}` : ""}`}>
                truncated
              </Badge>
            ) : null}
            {f.platform ? (
              <Badge variant="secondary" className="text-[10px]">
                {f.platform}
              </Badge>
            ) : null}
            {f.turn != null ? (
              <Badge variant="secondary" className="text-[10px]">
                turn {f.turn}
              </Badge>
            ) : null}
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
        <Button variant="ghost" size="sm" onClick={onBack}>
          ← Back
        </Button>
      </div>
      <div className="otel-facts-grid">
        <Fact label="duration">{fmtDurationMs(durationMs)}</Fact>
        <Fact label="model">
          {f.requestModel ? <span className="font-mono">{f.requestModel}</span> : null}
          {f.responseModel ? (
            <span className="ml-1 text-xs text-muted-foreground" title="the model named in the response, when it differs from the request">
              (served: {f.responseModel})
            </span>
          ) : null}
        </Fact>
        <Fact label="tokens">{tokens}</Fact>
        <Fact label="cost">
          {f.cost != null ? <span className="otel-c-cost">{fmtCostExact(f.cost)}</span> : <span className="text-muted-foreground">no pricing data</span>}
        </Fact>
        <Fact label="tools">{f.tools.length ? f.tools.join(", ") : null}</Fact>
        <Fact label="outcome">
          {f.finalStatus || f.exitReason ? `${f.finalStatus || ""}${f.finalStatus && f.exitReason ? " · " : ""}${f.exitReason || ""}` : null}
        </Fact>
        <Fact label="session">
          {f.session ? (
            <button
              type="button"
              className="otel-link font-mono"
              title="show this session's turns"
              onClick={() => navigate({ tab: "traces", source, view: "sessions", session: String(f.session), trace: "" })}
            >
              {f.session}
            </button>
          ) : null}
        </Fact>
      </div>
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

const first = (a: Record<string, any>, ...keys: string[]) => {
  for (const k of keys) if (a[k] != null && a[k] !== "") return a[k];
  return null;
};

/** A labelled attribute value in a summary: structured by default, raw on demand. */
function Attr({ a, label, keys, source }: { a: Record<string, any>; label: string; keys: string[]; source?: string }) {
  const key = keys.find((k) => a[k] != null && a[k] !== "");
  if (!key) return null;
  return (
    <ValueView
      attrKey={key}
      value={a[key]}
      label={label}
      onSessionClick={source ? (id) => navigate({ tab: "traces", source, view: "sessions", session: id, trace: "" }) : undefined}
    />
  );
}

// What a person wants first for each kind of span; the attribute table stays
// below, collapsed. Every kind the plugin emits has its own summary; anything
// else gets the generic one (name, duration, status, error).
export function SpanSummary({ span, source }: { span: TreeSpan; source?: string }) {
  const a = span._attrs || {};
  const kind = kindOf(span.name, a);
  const err = a["error.message"] ?? a["exception.message"];
  const errorBlock = err ? <ValueView attrKey="error.message" value={err} label={a["error.type"] ? `error · ${a["error.type"]}` : "error"} /> : null;

  if (kind === "tool") {
    const truncated = String(a["hermes.preview.output.truncated"]) === "true";
    const origChars = Number(a["hermes.preview.output.original_chars"]);
    return (
      <div className="space-y-2">
        <div className="flex flex-wrap items-center gap-2 text-xs">
          {first(a, "tool.name", "gen_ai.tool.name") ? (
            <Badge variant="secondary" className="font-mono text-[10px]">
              {String(first(a, "tool.name", "gen_ai.tool.name"))}
            </Badge>
          ) : null}
          <StatusBadge value={a["hermes.tool.outcome"] || a["status"]} />
          {a["hermes.tool.blocked_by"] ? (
            <Badge variant="destructive" className="text-[10px]">
              blocked by {String(a["hermes.tool.blocked_by"])}
            </Badge>
          ) : null}
          {a["hermes.tool.decided_by"] ? <span className="text-muted-foreground">decided by {String(a["hermes.tool.decided_by"])}</span> : null}
        </div>
        <Facts
          items={[
            { label: "target", value: a["hermes.tool.target"], mono: true },
            { label: "call id", value: a["gen_ai.tool.call.id"], mono: true },
            {
              label: "cpu avg / peak",
              value:
                a["hermes.tool.cpu.utilization.avg"] != null
                  ? `${a["hermes.tool.cpu.utilization.avg"]} / ${a["hermes.tool.cpu.utilization.peak"] ?? "?"}`
                  : null,
            },
            {
              label: "gpu avg / peak",
              value:
                a["hermes.tool.gpu.utilization.avg"] != null
                  ? `${a["hermes.tool.gpu.utilization.avg"]} / ${a["hermes.tool.gpu.utilization.peak"] ?? "?"}`
                  : null,
            },
          ]}
        />
        {errorBlock}
        <Attr a={a} label="command" keys={["hermes.tool.command"]} />
        <Attr a={a} label="arguments" keys={["input.value", "gen_ai.tool.call.arguments"]} />
        <Attr
          a={a}
          label={truncated ? `result · preview of ${Number.isFinite(origChars) ? fmtCount(origChars) : "?"} chars` : "result"}
          keys={["output.value", "gen_ai.tool.call.result"]}
        />
      </div>
    );
  }
  if (kind === "llm" || kind === "api") {
    const f = headerFacts(a);
    const http = first(a, "http.response.status_code", "gen_ai.response.status_code");
    return (
      <div className="space-y-2">
        <Facts
          items={[
            { label: "model", value: f.requestModel, mono: true },
            { label: "served by", value: f.responseModel, mono: true },
            {
              label: "tokens",
              value:
                f.totalTokens != null
                  ? `${fmtTokens(f.totalTokens)}${f.inputTokens != null ? ` (in ${fmtTokens(f.inputTokens)} · out ${fmtTokens(f.outputTokens ?? 0)}${f.reasoningTokens ? ` · reasoning ${fmtTokens(f.reasoningTokens)}` : ""}${f.cacheReadTokens ? ` · cache ${fmtTokens(f.cacheReadTokens)}` : ""})` : ""}`
                  : null,
            },
            { label: "cost", value: f.cost != null ? fmtCostExact(f.cost) : null },
            { label: "finish", value: first(a, "llm.response.finish_reason", "gen_ai.response.finish_reasons") },
            { label: "latency", value: a["llm.response.duration_ms"] != null ? fmtDurationMs(Number(a["llm.response.duration_ms"])) : null },
            { label: "messages", value: a["llm.request.message_count"] },
            { label: "mode", value: a["llm.api_mode"] },
            { label: "tool calls", value: a["llm.response.tool_calls"] },
            { label: "http", value: http, tone: http != null && Number(http) >= 400 ? "bad" : undefined },
            {
              label: "retries",
              value:
                a["hermes.retry.count"] != null ? `${a["hermes.retry.count"]}${a["hermes.max_retries"] != null ? ` of ${a["hermes.max_retries"]}` : ""}` : null,
            },
          ]}
        />
        {errorBlock}
        <Attr a={a} label={kind === "llm" ? "input" : "prompt"} keys={["llm.input_messages", "input.value", "gen_ai.input.messages"]} />
        <Attr a={a} label="response" keys={["llm.output.content", "output.value", "gen_ai.output.messages"]} />
        {a["gen_ai.system_instructions"] && !a["input.value"] ? <Attr a={a} label="system instructions" keys={["gen_ai.system_instructions"]} /> : null}
      </div>
    );
  }
  if (kind === "agent" || kind === "session") {
    const f = headerFacts(a);
    const tools = turnTools(a);
    const skills = splitList("hermes.turn.skills", a["hermes.turn.skills"]);
    return (
      <div className="space-y-2">
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <StatusBadge value={a["hermes.turn.final_status"]} />
          {a["hermes.session.kind"] ? (
            <Badge variant="secondary" className="text-[10px]">
              {String(a["hermes.session.kind"])}
            </Badge>
          ) : null}
          {String(a["hermes.session.is_subagent"]) === "true" ? (
            <Badge variant="secondary" className="text-[10px]">
              sub-agent
            </Badge>
          ) : null}
          {String(a["hermes.session.interrupted"]) === "true" ? (
            <Badge variant="destructive" className="text-[10px]">
              interrupted
            </Badge>
          ) : null}
          {String(a["hermes.session.failed"]) === "true" ? (
            <Badge variant="destructive" className="text-[10px]">
              failed
            </Badge>
          ) : null}
          {String(a["hermes.session.synthesized"]) === "true" ? (
            <Badge variant="secondary" className="text-[10px]" title="root recreated by the plugin after a restart">
              synthesized
            </Badge>
          ) : null}
        </div>
        <Facts
          items={[
            { label: "exit", value: a["hermes.turn.exit_reason"] },
            { label: "api calls", value: a["hermes.turn.api_call_count"] },
            { label: "tokens", value: f.totalTokens != null ? fmtTokens(f.totalTokens) : null },
            { label: "cost", value: f.cost != null ? fmtCostExact(f.cost) : f.costUnknown ? "no pricing data" : null },
            { label: "platform", value: a["hermes.platform"] },
            { label: "profile", value: a["hermes.profile"] },
            { label: "turn", value: a["hermes.turn.number"] },
            { label: "previous session", value: a["hermes.session.previous_id"], mono: true },
          ]}
        />
        {errorBlock}
        <Attr a={a} label="user message" keys={["input.value", "gen_ai.input.messages"]} source={source} />
        <Attr a={a} label="final response" keys={["output.value", "gen_ai.output.messages"]} />
        {tools.length ? (
          <Block label={`tools · ${tools.length}`}>
            <dl className="otel-attr-table otel-kv-table text-xs">
              {tools.map((t, i) => (
                <React.Fragment key={i}>
                  <dt className="font-mono">{t.tool || "·"}</dt>
                  <dd className="min-w-0 break-words">
                    <StatusBadge value={t.outcome} />
                    {t.command ? <code className="otel-code ml-1">{t.command}</code> : null}
                    {t.target ? <span className="ml-1 font-mono text-muted-foreground">{t.target}</span> : null}
                  </dd>
                </React.Fragment>
              ))}
            </dl>
          </Block>
        ) : null}
        {skills && skills.length ? (
          <Block label="skills">
            <Chips items={skills} mono />
          </Block>
        ) : null}
      </div>
    );
  }
  if (kind === "skill") {
    return (
      <div className="space-y-2">
        <div className="flex flex-wrap items-center gap-2 text-xs">
          {first(a, "hermes.skill.name", "gen_ai.skill.name") ? (
            <Badge variant="secondary" className="font-mono text-[10px]">
              {String(first(a, "hermes.skill.name", "gen_ai.skill.name"))}
            </Badge>
          ) : null}
          <StatusBadge value={a["hermes.skill.result_status"]} />
          {a["hermes.skill.source"] ? <span className="text-muted-foreground">loaded via {String(a["hermes.skill.source"])}</span> : null}
        </div>
        <Facts items={[{ label: "path", value: a["hermes.skill.path"], mono: true }]} />
        {errorBlock}
      </div>
    );
  }
  if (kind === "approval") {
    return (
      <div className="space-y-2">
        <div className="flex flex-wrap items-center gap-2 text-xs">
          {a["hermes.approval.choice"] ? (
            <Badge variant="secondary" className="text-[10px]">
              choice: {String(a["hermes.approval.choice"])}
            </Badge>
          ) : null}
          {a["hermes.approval.granted"] != null ? (
            <Badge variant={String(a["hermes.approval.granted"]) === "true" ? "secondary" : "destructive"} className="text-[10px]">
              {String(a["hermes.approval.granted"]) === "true" ? "granted" : "denied"}
            </Badge>
          ) : null}
          {String(a["hermes.approval.timed_out"]) === "true" ? (
            <Badge variant="destructive" className="text-[10px]">
              timed out
            </Badge>
          ) : null}
        </div>
        <Facts
          items={[
            { label: "decided by", value: a["hermes.approval.decided_by"] },
            { label: "surface", value: a["hermes.approval.surface"] },
            { label: "waited", value: a["hermes.approval.duration_ms"] != null ? fmtDurationMs(Number(a["hermes.approval.duration_ms"])) : null },
            { label: "pattern", value: first(a, "hermes.approval.pattern_key", "hermes.approval.pattern_keys"), mono: true },
          ]}
        />
        <Attr a={a} label="command" keys={["hermes.approval.command"]} />
        <Attr a={a} label="description" keys={["hermes.approval.description"]} />
      </div>
    );
  }
  if (kind === "subagent") {
    return (
      <div className="space-y-2">
        <div className="flex flex-wrap items-center gap-2 text-xs">
          {a["hermes.subagent.role"] ? (
            <Badge variant="secondary" className="text-[10px]">
              {String(a["hermes.subagent.role"])}
            </Badge>
          ) : null}
          <StatusBadge value={a["hermes.subagent.status"]} />
        </div>
        <Facts
          items={[
            { label: "duration", value: a["hermes.subagent.duration_ms"] != null ? fmtDurationMs(Number(a["hermes.subagent.duration_ms"])) : null },
            { label: "child session", value: first(a, "hermes.subagent.child_session_id", "hermes.subagent.child_id"), mono: true },
            { label: "parent session", value: first(a, "hermes.subagent.parent_session_id", "hermes.subagent.parent_id"), mono: true },
          ]}
        />
        {errorBlock}
        <Attr a={a} label="goal" keys={["hermes.subagent.goal", "input.value"]} />
        <Attr a={a} label="summary" keys={["hermes.subagent.summary", "output.value"]} />
      </div>
    );
  }
  if (kind === "cron") {
    return (
      <div className="space-y-2">
        <Facts items={[{ label: "job", value: a["hermes.cron.job_id"], mono: true }]} />
        {errorBlock}
        <Attr a={a} label="input" keys={["input.value"]} />
        <Attr a={a} label="output" keys={["output.value"]} />
      </div>
    );
  }
  // Generic summary for spans the plugin did not name (#283).
  return (
    <div className="space-y-2">
      <Facts
        items={[
          { label: "span", value: span.name, mono: true },
          { label: "duration", value: fmtDurationMs(span.durationMs) },
          { label: "status", value: span.status?.code === 2 ? "error" : span.status?.code === 1 ? "ok" : null },
          { label: "kind", value: a["openinference.span.kind"] || a["hermes.span_kind"] || null },
        ]}
      />
      {errorBlock}
      <Attr a={a} label="input" keys={["input.value"]} />
      <Attr a={a} label="output" keys={["output.value"]} />
    </div>
  );
}

// Keys the summary already shows in full; the table shows them collapsed.
const CONTENT_KEYS = new Set([
  "input.value",
  "output.value",
  "gen_ai.input.messages",
  "gen_ai.output.messages",
  "llm.input_messages",
  "llm.output.content",
  "gen_ai.tool.call.arguments",
  "gen_ai.tool.call.result",
  "gen_ai.system_instructions",
  "hermes.conversation.history",
]);

export function AttrGroups({ attrs, source }: { attrs: Record<string, any>; source?: string }) {
  const groups = groupAttrs(attrs || {});
  if (!groups.length) return <div className="text-xs text-muted-foreground">(no attributes)</div>;
  const onSession = source ? (id: string) => navigate({ tab: "traces", source, view: "sessions", session: id, trace: "" }) : undefined;
  return (
    <div className="space-y-3">
      {groups.map((g) => (
        <div key={g.prefix}>
          <MiniLabel>{g.prefix}</MiniLabel>
          <dl className="otel-attr-table text-xs">
            {g.entries.map((e) => (
              <React.Fragment key={e.key}>
                <dt className="text-muted-foreground" title={e.aliases.length ? `also: ${e.aliases.join(", ")}` : ""}>
                  {e.key}
                  {e.aliases.length ? <span className="ml-1 text-[10px] text-muted-foreground/60">+{e.aliases.length}</span> : null}
                </dt>
                <dd className="min-w-0 break-words text-foreground">
                  {CONTENT_KEYS.has(e.key) ? (
                    <details className="otel-details">
                      <summary className="cursor-pointer text-[11px] text-muted-foreground">{fmtCount(String(e.value).length)} chars</summary>
                      <div className="mt-1">
                        <ValueView attrKey={e.key} value={e.value} />
                      </div>
                    </details>
                  ) : (
                    <ValueView attrKey={e.key} value={e.value} onSessionClick={onSession} />
                  )}
                </dd>
              </React.Fragment>
            ))}
          </dl>
        </div>
      ))}
    </div>
  );
}

// Spans | Logs | Raw under a trace. The Logs sub-tab asks for the trace's own
// window (plus a margin) instead of a year (#283), and its rows carry the same
// actions as the Logs tab.
export function TraceTabs({
  traceId,
  source,
  logsAvailable,
  spans,
  raw,
  windowNs,
}: {
  traceId: string;
  source: string;
  logsAvailable: boolean;
  spans: any;
  raw: any;
  windowNs?: [number, number];
}) {
  const [tab, setTab] = useState<"spans" | "logs" | "raw">("spans");
  const [logs, setLogs] = useState<LogRec[] | null>(null);
  const [openLog, setOpenLog] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  useEffect(() => {
    if (tab !== "logs" || logs !== null) return;
    const p = withBackend(new URLSearchParams({ trace_id: traceId, limit: "500" }), source);
    const [startNs, endNs] = windowNs || [0, 0];
    if (startNs > 0 && endNs > 0) {
      // five minutes of margin on each side: lines logged around the turn
      p.set("start_s", String(Math.max(0, Math.floor(startNs / 1e9) - 300)));
      p.set("end_s", String(Math.ceil(endNs / 1e9) + 300));
    } else {
      p.set("lookback_hours", "720");
    }
    api(source === "live" ? "/live/logs/search" : "/logs/search", p)
      .then((r: any) => setLogs(r.logs || []))
      .catch((e: unknown) => {
        setError(e);
        setLogs([]);
      });
  }, [tab, logs, source, traceId, windowNs]);
  const rawText = useMemo(() => (tab === "raw" ? JSON.stringify(raw, null, 2) : ""), [raw, tab]);
  const actions = {
    onTrace: () => undefined,
    onSession: (id: string) => navigate({ tab: "logs", source, session: id, trace: "", lookback: "168" }),
    onEvent: (name: string) => navigate({ tab: "logs", source, trace: traceId, session: "", event: name, events: "1", lookback: "720" }),
    onContext: (l: LogRec) => navigate({ tab: "logs", source, trace: "", session: "", center: String(l.time_unix_nano || ""), lookback: "720" }),
  };
  return (
    <div className="space-y-3">
      <Segmented
        value={tab}
        onChange={setTab}
        label="trace detail view"
        options={[{ id: "spans", label: "Spans" }, ...(logsAvailable ? [{ id: "logs" as const, label: "Logs" }] : []), { id: "raw", label: "Raw" }]}
      />
      {tab === "spans" ? spans : null}
      {tab === "logs" ? (
        error ? (
          <ErrorBanner error={error} prefix="Logs" />
        ) : logs === null ? (
          <div className="text-xs text-muted-foreground">Loading…</div>
        ) : logs.length === 0 ? (
          <div className="border border-dashed border-border px-4 py-6 text-center text-xs text-muted-foreground">No log lines carry this trace id.</div>
        ) : (
          <div className="space-y-2">
            <div className="flex items-center justify-between text-xs text-muted-foreground">
              <span>
                {logs.length} line{logs.length === 1 ? "" : "s"} carry this trace id · click a line for its attributes
              </span>
              <button
                type="button"
                className="otel-link"
                title="open the Logs tab filtered to this trace"
                onClick={() => navigate({ tab: "logs", source, trace: traceId, session: "", lookback: "720" })}
              >
                open in Logs tab →
              </button>
            </div>
            <div className="otel-card-bg overflow-hidden border border-border font-mono text-xs">
              {logs.map((l, i) => {
                const k = String(l.seq ?? i);
                return <LogRow key={k} l={l} absolute expanded={openLog === k} onToggle={() => setOpenLog(openLog === k ? null : k)} actions={actions} />;
              })}
            </div>
          </div>
        )
      ) : null}
      {tab === "raw" ? (
        <div className="space-y-1">
          <div className="flex justify-end">
            <CopyButton text={rawText} label="copy JSON" />
          </div>
          <pre className="otel-pre otel-raw">{rawText}</pre>
        </div>
      ) : null}
    </div>
  );
}

export const kindColor = (name: string, attrs?: Record<string, any>) => KIND_COLOR[kindOf(name, attrs)];
export const toneClass = (bad: boolean) => cn(bad ? "otel-tone-bad" : "");
