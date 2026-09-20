import {
  React,
  useState,
  useEffect,
  useRef,
  useCallback,
  fetchJSON,
  API,
  Card,
  CardHeader,
  CardContent,
  Badge,
  Button,
  cn,
} from "./sdk";
import {
  fmtDurationMs,
  fmtAbsTime,
  fmtTimeAgo,
  fmtTokens,
  clip,
  traceAttrs,
  isMcpKeepalivePing,
  traceSpanCount,
  extractInputPreview,
  extractOutputPreview,
  buildSpanTree,
  liveTreeFromSpans,
  LiveSpan,
  LiveTrace,
} from "./lib";
import { categorize, IconCoins, IconChevronRight } from "./icons";
import { MiniLabel, ErrorBanner } from "./atoms";
import { SpanTreeView, LiveTraceCard, LiveTraceDetail } from "./spantree";
import { usePolling } from "./poll";
import { useSource, withBackend } from "./source";
import { SourceSelect } from "./sourceselect";
import { FilterBar, TraceFilters, DEFAULT_FILTERS, liveParams, backendParams, isDefaultFilters } from "./filters";
import { LiveSessions, BackendSessions, ViewToggle } from "./sessions";
import { TraceHeader, TraceTabs } from "./detail";
import { readNav, writeNav, NAV_EVENT, NavState } from "./nav";

/* eslint-disable @typescript-eslint/no-explicit-any */
const POLL_MS = 3000;
const VIEW_KEY = "hermes_otel.tracesView";

function readView(): "turns" | "sessions" {
  try {
    return localStorage.getItem(VIEW_KEY) === "sessions" ? "sessions" : "turns";
  } catch {
    return "turns";
  }
}

// ════════════════════════════════ LIVE SOURCE ════════════════════════════
// Rows come from the live store's own query (/live/traces, #184): filtering,
// grouping and totals happen in SQLite, the browser gets one page (#183).
function LiveTraces({ view, wanted }: { view: "turns" | "sessions"; wanted: NavState }) {
  const initial: TraceFilters = { ...DEFAULT_FILTERS, session: wanted.session || "", lookback: wanted.session || wanted.trace ? 168 : DEFAULT_FILTERS.lookback };
  const [filters, setFilters] = useState<TraceFilters>(initial);
  const [applied, setApplied] = useState<TraceFilters>(initial);
  const [rows, setRows] = useState<LiveTrace[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<LiveTrace | null>(null);
  const [detailSpans, setDetailSpans] = useState<LiveSpan[] | null>(null);
  const [showPings, setShowPings] = useState(false);
  const [paused, setPaused] = useState(false);
  const inflight = useRef(false);

  const load = useCallback(async () => {
    if (inflight.current) return;
    inflight.current = true;
    try {
      const r = await fetchJSON(`${API}/live/traces?${liveParams(applied)}`);
      setRows(r.traces || []);
      setTotal(r.total || 0);
      setError(null);
    } catch (e: any) {
      setError(String(e?.message || e));
    } finally {
      inflight.current = false;
      setLoading(false);
    }
  }, [applied]);
  useEffect(() => {
    setLoading(true);
    load();
  }, [load]);
  usePolling(load, POLL_MS, !paused && !selected && view === "turns");

  useEffect(() => {
    if (!selected) return;
    setDetailSpans(null);
    fetchJSON(`${API}/live/traces/${selected.traceId}`)
      .then((r: any) => setDetailSpans(r.spans || []))
      .catch(() => setDetailSpans([]));
  }, [selected]);

  // ?trace=<id> (a pasted link, or the Logs tab) opens that trace (#185).
  useEffect(() => {
    if (!wanted.trace) return;
    fetchJSON(`${API}/live/traces/${wanted.trace}`)
      .then((r: any) => {
        if (r.trace) setSelected({ ...r.trace, spans: r.spans });
      })
      .catch(() => setError(`Trace ${wanted.trace} is not in the live store`));
  }, [wanted.trace]);
  useEffect(() => {
    writeNav({ trace: selected ? String(selected.traceId) : "" });
  }, [selected]);

  const submit = () => setApplied(filters);

  if (selected) {
    const spans = detailSpans || [];
    const { roots } = liveTreeFromSpans(spans);
    const trace: LiveTrace = { ...selected, spans };
    return (
      <div className="space-y-2">
        {detailSpans === null ? <div className="text-xs text-muted-foreground">Loading spans…</div> : null}
        <LiveTraceDetail trace={trace} roots={roots} onBack={() => setSelected(null)} />
      </div>
    );
  }

  const pingCount = rows.filter((t) => isMcpKeepalivePing(t.rootName, t.error)).length;
  const shown = showPings ? rows : rows.filter((t) => !isMcpKeepalivePing(t.rootName, t.error));

  return (
    <div className="space-y-3">
      <Card>
        <CardContent className="space-y-3 pt-4">
          <FilterBar filters={filters} onChange={setFilters} onSubmit={submit} backend={false} busy={loading} />
        </CardContent>
      </Card>
      {error ? <ErrorBanner error={error} /> : null}
      {view === "sessions" ? (
        <LiveSessions filters={applied} onSelectTrace={setSelected} />
      ) : (
        <>
          <div className="flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
            <span>
              {shown.length} of {total} trace{total === 1 ? "" : "s"}
              {isDefaultFilters(applied) ? "" : " matching"} in the last {applied.lookback}h
            </span>
            <label className="inline-flex cursor-pointer items-center gap-1.5">
              <input type="checkbox" checked={showPings} onChange={(e: any) => setShowPings(e.target.checked)} />
              show MCP keepalive pings{pingCount ? ` (${pingCount})` : ""}
            </label>
            <Button variant="outline" size="sm" className="ml-auto" onClick={() => setPaused((p) => !p)}>
              {paused ? "▶ Resume" : "⏸ Pause"}
            </Button>
          </div>
          {shown.length === 0 ? (
            <div className="border border-dashed border-border px-4 py-12 text-center text-sm text-muted-foreground">
              <div className="mb-1 text-base font-medium text-foreground">{total ? "Nothing matched" : "No traces yet"}</div>
              {total ? "Widen the lookback or clear a filter." : "Run a Hermes turn — each turn appears here as a trace you can open into a span waterfall. No backend needed."}
            </div>
          ) : (
            <div className="flex flex-col gap-2">
              {shown.map((t) => (
                <LiveTraceCard key={t.traceId} trace={t} onSelect={setSelected} />
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}

// ═══════════════════════════════ BACKEND SOURCE ══════════════════════════
function StatusBar({ status, onRefresh }: { status: any; onRefresh: () => void }) {
  if (!status) return null;
  const configured = status.configured;
  const caps = [configured ? "traces" : null, status.metrics ? "metrics" : null, status.logs ? "logs" : null].filter(Boolean);
  return (
    <div className="flex items-start justify-between gap-3">
      <div className="min-w-0 flex-1 space-y-1.5">
        <div className="flex items-center gap-2">
          <span className={cn("h-2.5 w-2.5 rounded-full", configured ? "otel-pulse-dot" : "bg-muted-foreground/40")} />
          <span className="text-base font-semibold tracking-tight">{configured ? status.name || status.type : "Not configured"}</span>
          {configured && status.type && status.type !== status.name ? <Badge variant="secondary" className="text-[10px] uppercase">{status.type}</Badge> : null}
          {caps.map((c) => (
            <Badge key={c} variant="secondary" className="text-[10px]">{c}</Badge>
          ))}
          {status.query_backend_pin && status.query_backend_pin === status.active ? <span className="text-[10px] text-muted-foreground">default (query_backend)</span> : null}
        </div>
        {configured && status.query_url ? <div className="truncate font-mono text-xs text-muted-foreground">{status.query_url}</div> : null}
      </div>
      <Button variant="outline" size="sm" onClick={onRefresh}>Refresh</Button>
    </div>
  );
}

function BackendTraceCard({ trace, onSelect }: { trace: any; onSelect: (t: any) => void }) {
  const cat = categorize(trace.rootTraceName || "");
  const attrs = traceAttrs(trace);
  const startNs = trace.startTimeUnixNano ? Number(trace.startTimeUnixNano) : 0;
  const model = attrs["llm.model_name"] || attrs["gen_ai.response.model"];
  const toolName = attrs["tool.name"];
  const totalTokens = attrs["gen_ai.usage.total_tokens"] || attrs["llm.token_count.total"];
  const cost = attrs["hermes.cost.usage"];
  const isError = attrs["status"] === "error" || attrs["error.type"];
  const inP = clip(extractInputPreview(attrs), 140);
  const outP = clip(extractOutputPreview(attrs), 140);
  const spanCount = traceSpanCount(trace);
  const Icon = cat.Icon;
  return (
    <div
      className={cn("otel-card-bg otel-hover-parent flex cursor-pointer items-start gap-3 border p-3 transition-colors hover:bg-secondary/30", isError ? "border-destructive/30" : "border-border")}
      role="button"
      tabIndex={0}
      onClick={() => onSelect(trace)}
      onKeyDown={(e: any) => { if (e.key === "Enter") onSelect(trace); }}
      title={trace.traceID || trace.traceId}
    >
      <div className={cn("shrink-0 pt-0.5", cat.color)}><Icon size={16} /></div>
      <div className="min-w-0 flex-1 space-y-1">
        <div className="flex min-w-0 items-center gap-2">
          <span className="truncate font-mono text-sm">{trace.rootTraceName || "—"}</span>
          {cat.label ? <Badge variant="secondary" className="shrink-0 text-[10px]">{cat.label}</Badge> : null}
          {isError ? <Badge variant="destructive" className="shrink-0 text-[10px]">error</Badge> : null}
        </div>
        <div className="flex flex-wrap items-center gap-1">
          {toolName ? <Badge variant="secondary" className="font-mono text-[10px]">{String(toolName)}</Badge> : null}
          {model ? <Badge variant="secondary" className="font-mono text-[10px]">{String(model)}</Badge> : null}
        </div>
        {inP || outP ? (
          <div className="otel-pl-2 space-y-0.5 border-l-2 border-border/60 text-xs">
            {inP ? <div className="truncate text-foreground/80"><span className="otel-mr-2 text-[10px] text-muted-foreground">in</span>{inP}</div> : null}
            {outP ? <div className="truncate text-foreground/80"><span className="otel-mr-2 text-[10px] text-muted-foreground">out</span>{outP}</div> : null}
          </div>
        ) : null}
        <div className="flex flex-wrap items-center gap-x-2 text-xs text-muted-foreground">
          <span>{trace.rootServiceName || "—"}</span>
          <span className="text-border">·</span>
          <span className="tabular-nums">{fmtDurationMs(trace.durationMs)}</span>
          {spanCount != null ? <><span className="text-border">·</span><span className="tabular-nums">{spanCount} spans</span></> : null}
          {totalTokens != null ? <><span className="text-border">·</span><span className="inline-flex items-center gap-1 tabular-nums"><IconCoins size={12} className="opacity-70" />{fmtTokens(totalTokens)} tok</span></> : null}
          {cost ? <span className="tabular-nums text-emerald-400">${Number(cost).toFixed(4)}</span> : null}
          <span className="text-border">·</span>
          <span title={fmtAbsTime(startNs)}>{fmtTimeAgo(startNs)}</span>
        </div>
        <div className="truncate font-mono text-[10px] text-muted-foreground/60">{trace.traceID || trace.traceId}</div>
      </div>
      <div className="otel-self-center otel-reveal shrink-0 text-muted-foreground"><IconChevronRight size={16} /></div>
    </div>
  );
}

function BackendTraceDetail({ trace, detail, loading, error, onBack, source, status }: { trace: any; detail: any; loading: boolean; error: string | null; onBack: () => void; source: string; status: any }) {
  const tree = detail ? buildSpanTree(detail.batches || (detail.trace && detail.trace.batches)) : { roots: [], all: [] };
  const rootSpan = tree.roots[0] || null;
  const rootAttrs = rootSpan ? rootSpan._attrs : traceAttrs(trace);
  const durationMs = rootSpan ? rootSpan.durationMs : trace.durationMs;
  const traceId = String(trace.traceID || trace.traceId);
  const isError = tree.all.some((s) => (s.status?.code ?? s.status?.statusCode) === 2) || traceAttrs(trace)["status"] === "error";
  return (
    <Card>
      <CardHeader className="otel-space-y-0">
        <TraceHeader
          title={rootSpan?.name || trace.rootTraceName || "—"}
          traceId={traceId}
          service={trace.rootServiceName}
          durationMs={durationMs}
          rootAttrs={rootAttrs}
          spansAttrs={tree.all.map((s) => s._attrs)}
          error={isError}
          uiUrl={detail?.ui_url || null}
          uiLabel={status?.name || status?.type || null}
          source={source}
          onBack={onBack}
        />
      </CardHeader>
      <CardContent>
        {loading ? <div className="py-8 text-center text-sm text-muted-foreground">Loading trace…</div> : null}
        {error ? <ErrorBanner error={error} /> : null}
        {!loading && !error ? <TraceTabs traceId={traceId} source={source} logsAvailable={!!status?.logs} spans={<SpanTreeView roots={tree.roots} />} raw={detail} /> : null}
      </CardContent>
    </Card>
  );
}

function BackendTraces({ status, onRefresh, source, view, wanted }: { status: any; onRefresh: () => void; source: string; view: "turns" | "sessions"; wanted: NavState }) {
  const [filters, setFilters] = useState<TraceFilters>({ ...DEFAULT_FILTERS, session: wanted.session || "", lookback: wanted.session || wanted.trace ? 168 : DEFAULT_FILTERS.lookback });
  const [traces, setTraces] = useState<any[] | null>(null);
  const [showPings, setShowPings] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<any>(null);
  const [detail, setDetail] = useState<any>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);

  const search = useCallback(async () => {
    if (!status?.configured) return;
    setLoading(true);
    setError(null);
    setSelected(null);
    // A trace id opens that trace directly: no backend can search by id portably.
    const id = filters.traceId.trim();
    if (id) {
      setTraces([{ traceID: id, rootTraceName: "(by id)", spanSets: [] }]);
      setSelected({ traceID: id, rootTraceName: "(by id)" });
      setLoading(false);
      return;
    }
    try {
      const r = await fetchJSON(`${API}/traces/search?${backendParams(filters, source)}`);
      setTraces(r.traces || []);
    } catch (e: any) {
      setError(String(e?.message || e).replace(/^.*?:\s*/, ""));
      setTraces([]);
    } finally {
      setLoading(false);
    }
  }, [filters, status, source]);

  // A new source means a new result set.
  useEffect(() => {
    setTraces(null);
    setSelected(null);
    setError(null);
  }, [source]);

  // ?trace=<id> opens that trace on this backend (#185).
  useEffect(() => {
    if (wanted.trace && status?.configured) setSelected({ traceID: wanted.trace, rootTraceName: "(by id)" });
  }, [wanted.trace, status?.configured]);
  useEffect(() => {
    writeNav({ trace: selected ? String(selected.traceID || selected.traceId) : "" });
  }, [selected]);

  useEffect(() => {
    if (!selected) return;
    setDetail(null);
    setDetailError(null);
    setDetailLoading(true);
    const p = withBackend(new URLSearchParams(), source);
    fetchJSON(`${API}/traces/${selected.traceID || selected.traceId}?${p}`)
      .then(setDetail)
      .catch((e: any) => setDetailError(String(e?.message || e)))
      .finally(() => setDetailLoading(false));
  }, [selected, source]);

  if (!status?.configured)
    return (
      <Card>
        <CardContent className="space-y-2 pt-4 text-sm text-muted-foreground">
          <p>{status?.reason || "No queryable trace backend configured."}</p>
          <p className="text-xs">
            That's fine — the <span className="font-medium text-foreground">⚡ Live</span> source needs no backend.
            Add a backend of a queryable type to browse historical traces here:{" "}
            <span className="font-mono">{(status?.queryable_types || []).join(", ") || "none available"}</span>.
          </p>
        </CardContent>
      </Card>
    );

  if (selected) return <BackendTraceDetail trace={selected} detail={detail} loading={detailLoading} error={detailError} onBack={() => setSelected(null)} source={source} status={status} />;

  // Same default as the Live views: successful MCP keepalive pings stay out of
  // the list unless asked for. Backend rows carry status in the attributes.
  const isPing = (t: any) => isMcpKeepalivePing(t.rootTraceName, traceAttrs(t)["status"] === "error");
  const pingCount = traces ? traces.filter(isPing).length : 0;
  const shown = traces && !showPings ? traces.filter((t) => !isPing(t)) : traces;
  const renderCard = (t: any) => <BackendTraceCard key={t.traceID || t.traceId} trace={t} onSelect={setSelected} />;

  return (
    <div className="space-y-3">
      <Card>
        <CardContent className="space-y-3 pt-4">
          <StatusBar status={status} onRefresh={onRefresh} />
          <FilterBar filters={filters} onChange={setFilters} onSubmit={search} backend status={status} busy={loading} />
          <div className="flex items-center justify-between gap-3">
            <span className="text-xs text-muted-foreground">{shown == null ? "Not searched yet" : `${shown.length} trace${shown.length === 1 ? "" : "s"}`}</span>
            <label className="inline-flex cursor-pointer items-center gap-1.5 text-xs text-muted-foreground">
              <input type="checkbox" checked={showPings} onChange={(e: any) => setShowPings(e.target.checked)} />
              show MCP keepalive pings{pingCount ? ` (${pingCount})` : ""}
            </label>
          </div>
        </CardContent>
      </Card>
      {error ? (
        <div className="space-y-1">
          <ErrorBanner error={`Backend query failed: ${error}`} />
          <p className="px-1 text-xs text-muted-foreground">Backend unreachable from the dashboard. Use the ⚡ Live source — it reads the in-process store and always works.</p>
        </div>
      ) : null}
      {shown && shown.length > 0 ? (
        view === "sessions" ? (
          <BackendSessions traces={shown} renderTrace={renderCard} />
        ) : (
          <div className="flex flex-col gap-2">{shown.map(renderCard)}</div>
        )
      ) : shown && shown.length === 0 && !error ? (
        <div className="border border-dashed border-border px-4 py-8 text-center text-sm text-muted-foreground">
          {pingCount ? `Only MCP keepalive pings matched (${pingCount} hidden) — tick "show MCP keepalive pings" to see them.` : "No traces matched — widen the lookback or run a turn."}
        </div>
      ) : null}
    </div>
  );
}

// ═══════════════════════════════════ PAGE ════════════════════════════════
export function TracesPage() {
  const { source, setSource, status, refresh, isLive } = useSource();
  const [nav, setNav] = useState<NavState>(() => readNav());
  const [view, setViewState] = useState<"turns" | "sessions">((nav.view as any) || readView());
  const setView = (v: "turns" | "sessions") => {
    try {
      localStorage.setItem(VIEW_KEY, v);
    } catch {
      /* ignore */
    }
    setViewState(v);
    writeNav({ view: v });
  };

  // A navigation request from another tab (the Logs tab's trace ids, a session
  // link in a header) re-targets this page; the URL was already updated.
  useEffect(() => {
    const onNav = (e: any) => {
      const d: NavState = e.detail || {};
      if (d.tab && d.tab !== "traces") return;
      if (d.source) setSource(d.source);
      if (d.view) setViewState(d.view as any);
      setNav({ ...d });
    };
    window.addEventListener(NAV_EVENT, onNav);
    return () => window.removeEventListener(NAV_EVENT, onNav);
  }, [setSource]);
  useEffect(() => {
    writeNav({ tab: "traces", source, view });
  }, [source, view]);

  const key = `${source}:${nav.trace || ""}:${nav.session || ""}`;
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-3">
          <SourceSelect source={source} onChange={setSource} status={status} need="traces" />
          <ViewToggle view={view} onChange={setView} />
        </div>
        {isLive ? <MiniLabel>queried from the in-process store</MiniLabel> : null}
      </div>
      {isLive ? <LiveTraces key={key} view={view} wanted={nav} /> : <BackendTraces key={key} status={status} onRefresh={refresh} source={source} view={view} wanted={nav} />}
    </div>
  );
}
