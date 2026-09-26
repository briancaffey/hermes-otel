// Logs tab (#186): server-side search over the live store or a backend that
// serves logs, with level/logger/session/trace/text filters, absolute or
// relative times, paging, and trace ids that open the trace.
import { React, useState, useEffect, useCallback, useRef, fetchJSON, API, Button, Input, Select, SelectOption, cn } from "./sdk";
import { fmtTimeAgo, fmtAbsTime } from "./lib";
import { usePolling } from "./poll";
import { useSource, withBackend } from "./source";
import { SourceSelect } from "./sourceselect";
import { navigate } from "./nav";
import { LogFilters, DEFAULT_LOG_FILTERS, logParams } from "./params";
import { ErrorBanner } from "./atoms";

/* eslint-disable @typescript-eslint/no-explicit-any */
export type LogRec = {
  seq?: number;
  time_unix_nano?: number;
  level?: string;
  logger?: string;
  body?: string;
  trace_id?: string | null;
  session_id?: string | null;
};
const POLL_MS = 3000;
const PAGE = 200;

const LEVEL_CLASS: Record<string, string> = {
  ERROR: "text-destructive",
  CRITICAL: "text-destructive",
  WARNING: "otel-c-tool",
  WARN: "otel-c-tool",
  INFO: "otel-c-llm",
  DEBUG: "text-muted-foreground",
};

export function LogLine({ l, absolute, onTrace }: { l: LogRec; absolute: boolean; onTrace?: (id: string) => void }) {
  const lvl = (l.level || "INFO").toUpperCase();
  const ts = l.time_unix_nano || 0;
  return (
    <div className="flex items-start gap-2 border-b border-border/60 px-3 py-1 last:border-b-0">
      <span className={cn("shrink-0 text-muted-foreground/70", absolute ? "otel-w-40" : "otel-w-14")} title={ts ? fmtAbsTime(ts) : ""}>
        {ts ? (absolute ? fmtAbsTime(ts) : fmtTimeAgo(ts)) : ""}
      </span>
      <span className={cn("otel-w-12 shrink-0 font-semibold", LEVEL_CLASS[lvl] || "text-muted-foreground")}>{lvl}</span>
      {l.logger ? (
        <span className="otel-w-40 shrink-0 truncate text-muted-foreground" title={l.logger}>
          {l.logger}
        </span>
      ) : null}
      <span className="min-w-0 flex-1 whitespace-pre-wrap break-words text-foreground/90">{l.body}</span>
      {l.trace_id ? (
        <button
          type="button"
          className="otel-link shrink-0 font-mono text-[10px] text-muted-foreground/70"
          title={`open trace ${l.trace_id}`}
          onClick={() => onTrace?.(String(l.trace_id))}
        >
          {String(l.trace_id).slice(0, 8)}
        </button>
      ) : null}
    </div>
  );
}

export function LogsPage() {
  const { source, setSource, status, isLive } = useSource();
  const [filters, setFilters] = useState<LogFilters>(DEFAULT_LOG_FILTERS);
  const [applied, setApplied] = useState<LogFilters>(DEFAULT_LOG_FILTERS);
  const [logs, setLogs] = useState<LogRec[]>([]);
  const [loggers, setLoggers] = useState<{ logger: string; count: number }[]>([]);
  const [limit, setLimit] = useState(PAGE);
  const [absolute, setAbsolute] = useState(false);
  const [paused, setPaused] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [live, setLive] = useState<boolean | null>(null);
  const inflight = useRef(false);

  const base = isLive ? `${API}/live` : API;
  const canQuery = isLive || !!status?.logs;

  const load = useCallback(async () => {
    if (!canQuery || inflight.current) return;
    inflight.current = true;
    try {
      if (isLive) {
        const st = await fetchJSON(`${API}/live/status`);
        setLive(st && st.live !== false);
        if (!st || st.live === false) return;
      }
      const r = await fetchJSON(`${base}/logs/search?${logParams(applied, source, limit)}`);
      setLogs(r.logs || []);
      setError(null);
    } catch (e: any) {
      setError(String(e?.message || e));
    } finally {
      inflight.current = false;
    }
  }, [applied, base, canQuery, isLive, limit, source]);

  useEffect(() => {
    load();
  }, [load]);
  usePolling(load, POLL_MS, !paused && canQuery);

  useEffect(() => {
    if (!canQuery) return;
    const p = withBackend(new URLSearchParams(), source);
    fetchJSON(`${base}/loggers?${p}`)
      .then((r: any) => setLoggers(r.loggers || []))
      .catch(() => setLoggers([]));
  }, [base, canQuery, source]);

  const set = (k: keyof LogFilters, v: any) => setFilters({ ...filters, [k]: v });
  const openTrace = (id: string) => navigate({ tab: "traces", source, trace: id, view: "turns" });

  const header = (
    <div className="flex flex-wrap items-center justify-between gap-2">
      <div className="flex flex-wrap items-center gap-3">
        <SourceSelect source={source} onChange={setSource} status={status} need="logs" />
        <span className="text-xs text-muted-foreground">{logs.length} line{logs.length === 1 ? "" : "s"}</span>
      </div>
      <div className="flex items-center gap-2">
        <label className="inline-flex cursor-pointer items-center gap-1.5 text-xs text-muted-foreground">
          <input type="checkbox" checked={absolute} onChange={(e: any) => setAbsolute(e.target.checked)} />
          absolute times
        </label>
        <Button variant="outline" size="sm" onClick={() => setPaused((p) => !p)}>
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

  return (
    <div className="space-y-3">
      {header}
      <form
        className="otel-search-grid"
        onSubmit={(e: any) => {
          e.preventDefault();
          setLimit(PAGE);
          setApplied(filters);
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
          {loggers.map((l) => (
            <SelectOption key={l.logger} value={l.logger}>
              {l.logger} ({l.count})
            </SelectOption>
          ))}
        </Select>
        <Input className="h-8" placeholder="session id" value={filters.session} onChange={(e: any) => set("session", e.target.value)} />
        <Input className="h-8" placeholder="trace id" value={filters.traceId} onChange={(e: any) => set("traceId", e.target.value)} />
        <Input className="h-8" placeholder="text…" value={filters.text} onChange={(e: any) => set("text", e.target.value)} />
        <Select value={String(filters.lookback)} onValueChange={(v: string) => set("lookback", Number(v))} className="h-8">
          <SelectOption value="0.25">15m</SelectOption>
          <SelectOption value="1">1h</SelectOption>
          <SelectOption value="6">6h</SelectOption>
          <SelectOption value="24">24h</SelectOption>
          <SelectOption value="168">7d</SelectOption>
        </Select>
        <div className="flex items-center gap-2">
          <Button type="submit" size="sm">Search</Button>
          <Button type="button" variant="outline" size="sm" onClick={() => { setFilters(DEFAULT_LOG_FILTERS); setApplied(DEFAULT_LOG_FILTERS); setLimit(PAGE); }}>
            Clear
          </Button>
        </div>
      </form>
      {error ? <ErrorBanner error={error} /> : null}
      {isLive && live === false ? (
        <div className="border border-dashed border-border px-4 py-12 text-center text-sm text-muted-foreground">
          <div className="mb-1 text-base font-medium text-foreground">Live mode is off</div>
          Set <span className="font-mono">dashboard_live: true</span> and <span className="font-mono">capture_logs: true</span>, then run a turn.
        </div>
      ) : logs.length === 0 ? (
        <div className="border border-dashed border-border px-4 py-12 text-center text-sm text-muted-foreground">
          <div className="mb-1 text-base font-medium text-foreground">No log lines</div>
          {isLive ? (
            <>
              Set <span className="font-mono">capture_logs: true</span> in the plugin config and run a turn — the agent's log lines stream here. Lines written while a
              turn is in flight carry its trace and session id.
            </>
          ) : (
            "Nothing matched in this window."
          )}
        </div>
      ) : (
        <>
          <div className="otel-card-bg overflow-hidden border border-border font-mono text-xs">
            {logs.map((l, i) => (
              <LogLine key={l.seq ?? i} l={l} absolute={absolute} onTrace={openTrace} />
            ))}
          </div>
          {logs.length >= limit ? (
            <div className="flex justify-center">
              <Button variant="outline" size="sm" onClick={() => setLimit((n) => n + PAGE)}>
                Load {PAGE} more
              </Button>
            </div>
          ) : null}
        </>
      )}
    </div>
  );
}
