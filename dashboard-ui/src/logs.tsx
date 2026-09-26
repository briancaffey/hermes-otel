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
import { React, useState, useEffect, useCallback, useRef, fetchJSON, API, Button, Input, Select, SelectOption, cn } from "./sdk";
import { fmtTimeAgo, fmtAbsTime } from "./lib";
import { usePolling } from "./poll";
import { useSource, withBackend } from "./source";
import { SourceSelect } from "./sourceselect";
import { navigate, readNav, writeNav } from "./nav";
import { LogFilters, DEFAULT_LOG_FILTERS, LOG_PAGE_SIZES, logParams, logFiltersFromNav, navFromLogFilters, logPageSizeFromNav } from "./params";
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
        <Select value={String(pageSize)} onValueChange={(v: string) => { setPageSize(Number(v)); setCursors([]); }} className="h-8">
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
      <span>
        {logs.length
          ? `${fmtAbsTime(oldestShown)} → ${fmtAbsTime(newestShown)}`
          : ""}
      </span>
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
          {filters.logger && !loggers.some((l) => l.logger === filters.logger) ? (
            <SelectOption value={filters.logger}>{filters.logger}</SelectOption>
          ) : null}
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
          <SelectOption value="720">30d</SelectOption>
        </Select>
        <div className="flex items-center gap-2">
          <Button type="submit" size="sm">Search</Button>
          <Button type="button" variant="outline" size="sm" onClick={() => { setFilters(DEFAULT_LOG_FILTERS); apply(DEFAULT_LOG_FILTERS); }}>
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
          <div className="mb-1 text-base font-medium text-foreground">{onNewestPage ? "No log lines" : "No older lines"}</div>
          {!onNewestPage ? (
            <Button variant="outline" size="sm" onClick={newer}>← Back to the newer page</Button>
          ) : isLive ? (
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
          {pager}
          <div className="otel-card-bg overflow-hidden border border-border font-mono text-xs">
            {logs.map((l, i) => (
              <LogLine key={l.seq ?? `${l.time_unix_nano || 0}:${i}`} l={l} absolute={absolute} onTrace={openTrace} />
            ))}
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
