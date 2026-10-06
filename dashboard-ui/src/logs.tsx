// Logs tab (#186): server-side search over the live store or a backend that
// serves logs, with level/logger/session/trace/text filters, absolute or
// relative times, keyset paging, and trace ids that open the trace.
//
// Paging is by cursor, not offset: a page is "the N newest rows older than
// `before`", where `before` is the oldest row of the previous page. New lines
// arriving never shift an older page, and every backend bounds time natively
// (an offset would cost O(offset) and Loki has none). The newest page keeps
// polling; browsing older pages pauses it. Filters, page size and the cursor
// live in the URL, so refresh, back and a pasted link land on the same page.
import { React, useState, useEffect, useCallback, useMemo, useRef, fetchJSON, API, Button, Input, Select, SelectOption, cn } from "./sdk";
import { fmtTimeAgo, fmtAbsTime } from "./lib";
import { usePolling } from "./poll";
import { useSource, withBackend } from "./source";
import { SourceSelect } from "./sourceselect";
import { navigate, readNav, writeNav } from "./nav";
import { LogFilters, DEFAULT_LOG_FILTERS, LOG_PAGE_SIZES, logParams, logFiltersFromNav, navFromLogFilters, logPageSizeFromNav } from "./params";
import { ErrorBanner } from "./atoms";
import { CopyButton } from "./detail";
import { severityCounts, timeBuckets, groupLogAttributes, codeLocation, attributionHint, severityOf, type Bucket } from "./logs-lib";

export type LogRec = {
  seq?: number;
  time_unix_nano?: number;
  level?: string;
  severity_number?: number | null;
  logger?: string;
  body?: string;
  trace_id?: string | null;
  span_id?: string | null;
  session_id?: string | null;
  event_name?: string | null;
  attributes?: Record<string, unknown> | null;
};
const POLL_MS = 3000;

const LEVEL_CLASS: Record<string, string> = {
  ERROR: "text-destructive",
  CRITICAL: "text-destructive",
  FATAL: "text-destructive",
  WARNING: "otel-c-tool",
  WARN: "otel-c-tool",
  INFO: "otel-c-llm",
  DEBUG: "text-muted-foreground",
};

export type LogRowActions = {
  onTrace?: (id: string) => void;
  onSession?: (id: string) => void;
  onContext?: (l: LogRec) => void;
  onEvent?: (name: string) => void;
};

/** One log line; click to expand its attributes, exception and code location (#268). */
export function LogRow({
  l,
  absolute,
  wrap = true,
  expanded,
  onToggle,
  actions,
}: {
  l: LogRec;
  absolute: boolean;
  wrap?: boolean;
  expanded?: boolean;
  onToggle?: () => void;
  actions?: LogRowActions;
}) {
  const lvl = (l.level || "INFO").toUpperCase();
  const ts = l.time_unix_nano || 0;
  const attrs = (l.attributes || {}) as Record<string, any>;
  const hint = attributionHint(attrs);
  const isError = severityOf(lvl) === "ERROR";
  const edge = isError ? "border-l-2 border-l-primary" : "";
  return (
    <div className={cn("border-b border-border/60 last:border-b-0", expanded ? "bg-muted/30" : "otel-hoverable", edge)}>
      <div
        className="flex cursor-pointer items-start gap-2 px-3 py-1"
        onClick={onToggle}
        role="button"
        tabIndex={0}
        title={expanded ? "collapse" : "expand attributes"}
      >
        <span className={cn("shrink-0 text-muted-foreground/70", absolute ? "otel-w-40" : "otel-w-14")} title={ts ? fmtAbsTime(ts) : ""}>
          {ts ? (absolute ? fmtAbsTime(ts) : fmtTimeAgo(ts)) : ""}
        </span>
        <span className={cn("otel-w-12 shrink-0 font-semibold", LEVEL_CLASS[lvl] || "text-muted-foreground")}>{lvl}</span>
        {l.event_name ? (
          <button
            type="button"
            className="shrink-0 rounded border border-border px-1 font-mono text-[10px] text-muted-foreground"
            title="structured event — click to filter to this event"
            onClick={(e: any) => {
              e.stopPropagation();
              actions?.onEvent?.(String(l.event_name));
            }}
          >
            {l.event_name}
          </button>
        ) : null}
        {l.logger && !l.event_name ? (
          <span className="otel-w-40 shrink-0 truncate text-muted-foreground" title={l.logger}>
            {l.logger}
          </span>
        ) : null}
        <span className={cn("min-w-0 flex-1 text-foreground/90", wrap ? "whitespace-pre-wrap break-words" : "truncate")}>{l.body}</span>
        {hint ? (
          <span className="shrink-0 text-[10px] text-muted-foreground/70" title={`attributed by ${hint.title}`}>
            {hint.text}
          </span>
        ) : null}
        {l.trace_id ? (
          <button
            type="button"
            className="otel-link shrink-0 font-mono text-[10px] text-muted-foreground/70"
            title={`open trace ${l.trace_id}`}
            onClick={(e: any) => {
              e.stopPropagation();
              actions?.onTrace?.(String(l.trace_id));
            }}
          >
            {String(l.trace_id).slice(0, 8)}
          </button>
        ) : null}
      </div>
      {expanded ? <LogDetail l={l} actions={actions} /> : null}
    </div>
  );
}

function LogDetail({ l, actions }: { l: LogRec; actions?: LogRowActions }) {
  const attrs = (l.attributes || {}) as Record<string, any>;
  const { groups, stacktrace } = groupLogAttributes(attrs);
  const where = codeLocation(attrs);
  const ts = l.time_unix_nano || 0;
  return (
    <div className="space-y-2 border-t border-border/60 px-3 py-2 font-mono text-[11px]">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-muted-foreground">
        <span>{ts ? fmtAbsTime(ts) : ""}</span>
        {l.logger ? <span title="logger / instrumentation scope">{l.logger}</span> : null}
        {l.severity_number != null ? <span title="OTel severity number">sev {l.severity_number}</span> : null}
        {where ? <span title="code location">{where}</span> : null}
        {l.session_id ? (
          <button type="button" className="otel-link" title="show this session's log lines" onClick={() => actions?.onSession?.(String(l.session_id))}>
            session {String(l.session_id)}
          </button>
        ) : null}
        {l.trace_id ? (
          <button type="button" className="otel-link" title="open the trace" onClick={() => actions?.onTrace?.(String(l.trace_id))}>
            trace {String(l.trace_id)}
          </button>
        ) : null}
        {l.span_id ? <span title="span id">span {l.span_id}</span> : null}
        <span className="ml-auto flex items-center gap-2">
          {ts && actions?.onContext ? (
            <button type="button" className="otel-link" title="show every line within 30 s of this one" onClick={() => actions.onContext?.(l)}>
              ±30 s around this line
            </button>
          ) : null}
          <CopyButton text={JSON.stringify(l, null, 2)} label="copy JSON" />
        </span>
      </div>
      {l.body ? <pre className="otel-pre otel-raw whitespace-pre-wrap break-words">{l.body}</pre> : null}
      {groups.length ? (
        <div className="grid gap-x-4 gap-y-1 sm:grid-cols-2">
          {groups.map((g) => (
            <div key={g.label} className="min-w-0">
              <div className="mb-1 text-muted-foreground">{g.label}</div>
              <table className="otel-kv-table w-full">
                <tbody>
                  {g.entries.map(([k, v]) => (
                    <tr key={k}>
                      <td className="otel-kv text-muted-foreground">{k}</td>
                      <td className="break-all text-foreground/90">{typeof v === "string" ? v : JSON.stringify(v)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ))}
        </div>
      ) : (
        <div className="text-muted-foreground">No attributes on this record.</div>
      )}
      {stacktrace ? <pre className="otel-pre otel-raw max-h-80 overflow-auto whitespace-pre-wrap break-words">{stacktrace}</pre> : null}
    </div>
  );
}

/** Severity counts for the rows shown plus a per-bucket sparkline (errors on top). */
export function SeveritySummary({ rows, buckets }: { rows: LogRec[]; buckets: Bucket[] }) {
  const c = severityCounts(rows);
  const max = Math.max(1, ...buckets.map((b) => b.total));
  const w = 160;
  const h = 24;
  const bw = buckets.length ? w / buckets.length : w;
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
      <span className="tabular-nums">
        {c.total} shown
        {c.events ? ` · ${c.events} event${c.events === 1 ? "" : "s"}` : ""}
      </span>
      <span className="tabular-nums">
        <span className={c.bySeverity.ERROR ? "text-destructive" : ""}>{c.bySeverity.ERROR} error</span>
        {" · "}
        <span className={c.bySeverity.WARN ? "otel-c-tool" : ""}>{c.bySeverity.WARN} warn</span>
        {" · "}
        {c.bySeverity.INFO} info
        {c.bySeverity.DEBUG ? ` · ${c.bySeverity.DEBUG} debug` : ""}
      </span>
      {buckets.length > 1 ? (
        <svg width={w} height={h} role="img" aria-label="lines per time bucket, oldest left" className="shrink-0">
          <rect x="0" y={h - 1} width={w} height="1" className="text-muted-foreground" fill="currentColor" opacity="0.3" />
          {buckets.map((b, i) => {
            const total = (b.total / max) * (h - 2);
            const bad = ((b.errors + b.warns) / max) * (h - 2);
            return (
              <g key={i}>
                <title>{`${b.total} line${b.total === 1 ? "" : "s"}${b.errors ? `, ${b.errors} error` : ""}${b.warns ? `, ${b.warns} warn` : ""}`}</title>
                <rect
                  x={i * bw + 0.5}
                  y={h - 1 - total}
                  width={Math.max(1, bw - 1)}
                  height={total}
                  className="text-muted-foreground"
                  fill="currentColor"
                  opacity="0.35"
                />
                {bad > 0 ? (
                  <rect
                    x={i * bw + 0.5}
                    y={h - 1 - bad}
                    width={Math.max(1, bw - 1)}
                    height={bad}
                    className={b.errors ? "text-destructive" : "otel-c-tool"}
                    fill="currentColor"
                  />
                ) : null}
              </g>
            );
          })}
        </svg>
      ) : null}
    </div>
  );
}

export function LogsPage() {
  const { source, setSource, status, isLive } = useSource();
  const initialNav = readNav();
  const [filters, setFilters] = useState<LogFilters>(() => logFiltersFromNav(initialNav));
  const [applied, setApplied] = useState<LogFilters>(() => logFiltersFromNav(initialNav));
  const [pageSize, setPageSize] = useState<number>(() => logPageSizeFromNav(initialNav.size));
  // Cursor stack: [] = newest page; each entry is the `before` of one page
  // deeper, so "Newer" is a pop and a refresh can rebuild the top from the URL.
  const [cursors, setCursors] = useState<string[]>(() => (initialNav.before && /^\d+$/.test(initialNav.before) ? [initialNav.before] : []));
  const [logs, setLogs] = useState<LogRec[]>([]);
  const [nextBefore, setNextBefore] = useState<string | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [loggers, setLoggers] = useState<{ logger: string; count: number }[]>([]);
  const [absolute, setAbsolute] = useState(false);
  const [paused, setPaused] = useState(false);
  // follow = oldest first with the newest line at the bottom (a tail); wrap = long bodies wrap
  const [follow, setFollow] = useState(false);
  const [wrap, setWrap] = useState(true);
  const [expanded, setExpanded] = useState<string | null>(null);
  const listEnd = useRef<any>(null);
  const [error, setError] = useState<string | null>(null);
  const [live, setLive] = useState<boolean | null>(null);
  const inflight = useRef(false);
  const before = cursors.length ? cursors[cursors.length - 1] : null;
  const onNewestPage = cursors.length === 0;
  const base = isLive ? `${API}/live` : API;
  const canQuery = isLive || !!status?.logs;

  // Keep the URL in step with what is applied: filters, page size, cursor.
  useEffect(() => {
    writeNav({ ...navFromLogFilters(applied), size: pageSize !== 200 ? String(pageSize) : "", before: before || "" });
  }, [applied, pageSize, before]);

  const load = useCallback(async () => {
    if (!canQuery || inflight.current) return;
    inflight.current = true;
    try {
      if (isLive) {
        const st = await fetchJSON(`${API}/live/status`);
        setLive(st && st.live !== false);
        if (!st || st.live === false) return;
      }
      const r = await fetchJSON(`${base}/logs/search?${logParams(applied, source, pageSize, before)}`);
      setLogs(dedupe(r.logs || []));
      setNextBefore(r.next_before_ns != null ? String(r.next_before_ns) : null);
      setHasMore(!!r.has_more);
      setError(null);
    } catch (e: any) {
      setError(String(e?.message || e));
    } finally {
      inflight.current = false;
    }
  }, [applied, base, before, canQuery, isLive, pageSize, source]);
  useEffect(() => {
    load();
  }, [load]);
  // Only the newest page follows new lines; an older page is a fixed window.
  usePolling(load, POLL_MS, !paused && canQuery && onNewestPage);
  useEffect(() => {
    if (!canQuery) return;
    const p = withBackend(new URLSearchParams(), source);
    fetchJSON(`${base}/loggers?${p}`)
      .then((r: any) => setLoggers(r.loggers || []))
      .catch(() => setLoggers([]));
  }, [base, canQuery, source]);

  const set = (k: keyof LogFilters, v: any) => setFilters({ ...filters, [k]: v });
  const apply = (f: LogFilters) => {
    setApplied(f);
    setCursors([]);
  };
  const older = () => {
    if (nextBefore) setCursors((c) => [...c, nextBefore]);
  };
  const newer = () => setCursors((c) => c.slice(0, -1));
  const newest = () => setCursors([]);
  const openTrace = (id: string) => navigate({ tab: "traces", source, trace: id, view: "turns" });
  const showSession = (id: string) => {
    const f = { ...DEFAULT_LOG_FILTERS, session: id, lookback: applied.lookback };
    setFilters(f);
    apply(f);
  };
  const showContext = (l: LogRec) => {
    const f = { ...DEFAULT_LOG_FILTERS, centerNs: String(l.time_unix_nano || ""), windowS: 30, lookback: applied.lookback };
    setFilters(f);
    apply(f);
  };
  const showEvent = (name: string) => {
    const f = { ...filters, eventName: name, eventsOnly: true };
    setFilters(f);
    apply(f);
  };
  const actions: LogRowActions = { onTrace: openTrace, onSession: showSession, onContext: showContext, onEvent: showEvent };
  const rowKey = (l: LogRec, i: number) => String(l.seq ?? `${l.time_unix_nano || 0}:${i}`);
  const ordered = useMemo(() => (follow ? [...logs].reverse() : logs), [logs, follow]);
  const buckets = useMemo(() => timeBuckets(logs, 24), [logs]);
  useEffect(() => {
    if (follow && onNewestPage && !paused) listEnd.current?.scrollIntoView?.({ block: "nearest" });
  }, [logs, follow, onNewestPage, paused]);
  const permalink = typeof window !== "undefined" ? window.location.href : "";
  const oldestShown = logs.length ? logs[logs.length - 1].time_unix_nano || 0 : 0;
  const newestShown = logs.length ? logs[0].time_unix_nano || 0 : 0;

  const header = (
    <div className="flex flex-wrap items-center justify-between gap-2">
      <div className="flex flex-wrap items-center gap-3">
        <SourceSelect source={source} onChange={setSource} status={status} need="logs" />
        <span className="text-xs text-muted-foreground">
          {logs.length} line{logs.length === 1 ? "" : "s"}
          {cursors.length ? ` · page ${cursors.length + 1}` : ""}
          {onNewestPage ? (paused ? " · paused" : " · following") : " · older page, not following"}
        </span>
      </div>
      <div className="flex items-center gap-2">
        <label className="inline-flex cursor-pointer items-center gap-1.5 text-xs text-muted-foreground">
          <input type="checkbox" checked={absolute} onChange={(e: any) => setAbsolute(e.target.checked)} />
          absolute times
        </label>
        <label
          className="inline-flex cursor-pointer items-center gap-1.5 text-xs text-muted-foreground"
          title="oldest first, newest at the bottom, scrolls with new lines"
        >
          <input type="checkbox" checked={follow} onChange={(e: any) => setFollow(e.target.checked)} />
          follow
        </label>
        <label className="inline-flex cursor-pointer items-center gap-1.5 text-xs text-muted-foreground" title="wrap long lines">
          <input type="checkbox" checked={wrap} onChange={(e: any) => setWrap(e.target.checked)} />
          wrap
        </label>
        <CopyButton text={permalink} label="copy link" />
        <Select
          value={String(pageSize)}
          onValueChange={(v: string) => {
            setPageSize(Number(v));
            setCursors([]);
          }}
          className="h-8"
        >
          {LOG_PAGE_SIZES.map((n) => (
            <SelectOption key={n} value={String(n)}>
              {n} / page
            </SelectOption>
          ))}
        </Select>
        <Button variant="outline" size="sm" onClick={() => setPaused((p) => !p)} disabled={!onNewestPage}>
          {paused ? "▶ Resume" : "⏸ Pause"}
        </Button>
      </div>
    </div>
  );
  if (!canQuery)
    return (
      <div className="space-y-3">
        {header}
        <div className="border border-dashed border-border px-4 py-12 text-center text-sm text-muted-foreground">
          <div className="mb-1 text-base font-medium text-foreground">This source does not serve logs</div>
          Pick the Live source, or a backend whose adapter serves logs (OpenObserve, SigNoz, Uptrace, LGTM).
        </div>
      </div>
    );

  const pager = (
    <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground">
      <span>{logs.length ? `${fmtAbsTime(oldestShown)} → ${fmtAbsTime(newestShown)}` : ""}</span>
      <div className="flex items-center gap-2">
        <Button variant="outline" size="sm" onClick={newest} disabled={onNewestPage}>
          ⏮ Newest
        </Button>
        <Button variant="outline" size="sm" onClick={newer} disabled={onNewestPage}>
          ← Newer
        </Button>
        <Button variant="outline" size="sm" onClick={older} disabled={!hasMore || !nextBefore}>
          Older →
        </Button>
      </div>
    </div>
  );

  return (
    <div className="space-y-3">
      {header}
      <form
        className="otel-search-grid"
        onSubmit={(e: any) => {
          e.preventDefault();
          apply(filters);
        }}
      >
        <Select value={filters.minLevel} onValueChange={(v: string) => set("minLevel", v)} className="h-8">
          <SelectOption value="0">All levels</SelectOption>
          <SelectOption value="20">Info+</SelectOption>
          <SelectOption value="30">Warn+</SelectOption>
          <SelectOption value="40">Error</SelectOption>
        </Select>
        <Select value={filters.logger} onValueChange={(v: string) => set("logger", v)} className="h-8">
          <SelectOption value="">Any logger</SelectOption>
          {filters.logger && !loggers.some((l) => l.logger === filters.logger) ? <SelectOption value={filters.logger}>{filters.logger}</SelectOption> : null}
          {loggers.map((l) => (
            <SelectOption key={l.logger} value={l.logger}>
              {l.logger} ({l.count})
            </SelectOption>
          ))}
        </Select>
        <Input className="h-8" placeholder="session id" value={filters.session} onChange={(e: any) => set("session", e.target.value)} />
        <Input className="h-8" placeholder="trace id" value={filters.traceId} onChange={(e: any) => set("traceId", e.target.value)} />
        <Input className="h-8" placeholder="text…" value={filters.text} onChange={(e: any) => set("text", e.target.value)} />
        <label
          className="inline-flex h-8 cursor-pointer items-center gap-1.5 text-[11px] text-muted-foreground"
          title="only hermes.* / GenAI events (logs.events.enabled)"
        >
          <input type="checkbox" checked={filters.eventsOnly || !!filters.eventName} onChange={(e: any) => set("eventsOnly", e.target.checked)} />
          events only
        </label>
        <Input
          className="h-8 font-mono"
          placeholder="event name…"
          value={filters.eventName}
          onChange={(e: any) => set("eventName", e.target.value)}
          title="one structured event, e.g. hermes.tool.call"
        />
        <Select value={String(filters.lookback)} onValueChange={(v: string) => set("lookback", Number(v))} className="h-8">
          <SelectOption value="0.25">15m</SelectOption>
          <SelectOption value="1">1h</SelectOption>
          <SelectOption value="6">6h</SelectOption>
          <SelectOption value="24">24h</SelectOption>
          <SelectOption value="168">7d</SelectOption>
          <SelectOption value="720">30d</SelectOption>
        </Select>
        <div className="flex items-center gap-2">
          <Button type="submit" size="sm">
            Search
          </Button>
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => {
              setFilters(DEFAULT_LOG_FILTERS);
              apply(DEFAULT_LOG_FILTERS);
            }}
          >
            Clear
          </Button>
        </div>
      </form>
      {error ? <ErrorBanner error={error} /> : null}
      {isLive && live === false ? (
        <div className="border border-dashed border-border px-4 py-12 text-center text-sm text-muted-foreground">
          <div className="mb-1 text-base font-medium text-foreground">Live mode is off</div>
          Set <span className="font-mono">dashboard_live: true</span> and <span className="font-mono">logs.capture: true</span> (or{" "}
          <span className="font-mono">logs.events.enabled: true</span>), then run a turn.
        </div>
      ) : logs.length === 0 ? (
        <div className="border border-dashed border-border px-4 py-12 text-center text-sm text-muted-foreground">
          <div className="mb-1 text-base font-medium text-foreground">{onNewestPage ? "No log lines" : "No older lines"}</div>
          {!onNewestPage ? (
            <Button variant="outline" size="sm" onClick={newer}>
              ← Back to the newer page
            </Button>
          ) : isLive ? (
            <>
              Set <span className="font-mono">logs.capture: true</span> or <span className="font-mono">logs.events.enabled: true</span> in the plugin config and
              run a turn — the agent's log lines and events stream here. Lines written while a turn is in flight carry its trace and session id; click a line
              for its attributes.
            </>
          ) : (
            "Nothing matched in this window."
          )}
        </div>
      ) : (
        <>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <SeveritySummary rows={logs} buckets={buckets} />
            {applied.centerNs ? (
              <span className="text-xs text-muted-foreground">
                ±{applied.windowS} s around {fmtAbsTime(Number(applied.centerNs))}{" "}
                <button
                  type="button"
                  className="otel-link"
                  onClick={() => {
                    const f = { ...applied, centerNs: "" };
                    setFilters(f);
                    apply(f);
                  }}
                >
                  clear
                </button>
              </span>
            ) : null}
          </div>
          {pager}
          <div className="otel-card-bg overflow-hidden border border-border font-mono text-xs">
            {ordered.map((l, i) => {
              const k = rowKey(l, i);
              return (
                <LogRow
                  key={k}
                  l={l}
                  absolute={absolute}
                  wrap={wrap}
                  expanded={expanded === k}
                  onToggle={() => setExpanded(expanded === k ? null : k)}
                  actions={actions}
                />
              );
            })}
            <div ref={listEnd} />
          </div>
          {pager}
        </>
      )}
    </div>
  );
}

/** Backends that bound time coarser than a nanosecond can hand back a row the
 *  cursor was taken from; drop exact repeats within a page defensively. */
export function dedupe(rows: LogRec[]): LogRec[] {
  const seen = new Set<string>();
  const out: LogRec[] = [];
  for (const r of rows) {
    const k = `${r.time_unix_nano || 0}|${r.logger || ""}|${r.body || ""}`;
    if (seen.has(k)) continue;
    seen.add(k);
    out.push(r);
  }
  return out;
}
