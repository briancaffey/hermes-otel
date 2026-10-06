// Traces tab (#183, #281): one search bar, one card, cursor paging and URL
// state for both sources. Rows come from the live store's own query
// (/live/traces, #184) or a backend adapter (/traces/search); both answer
// the same page shape (contract §3) and the cards are built from one
// normalised row (lib.TraceRow).
import { React, useState, useEffect, useRef, useCallback, useMemo, api, Card, CardHeader, CardContent, Badge, Button, cn } from "./sdk";
import { buildSpanTree, liveTreeFromSpans, LiveSpan, LiveTrace, TraceRow, rowFromLive, rowFromBackend, isMcpKeepalivePing, traceAttrs, findRoot } from "./lib";
import { MiniLabel, ErrorBanner, Pager, Segmented, Empty, Toggle } from "./atoms";
import { IconPause, IconPlay } from "./icons";
import { SpanTreeView, TraceCard, LiveTraceDetail } from "./spantree";
import { usePolling } from "./poll";
import { useSource, withBackend } from "./source";
import { SourceSelect } from "./sourceselect";
import { FilterBar, TraceFilters, DEFAULT_FILTERS, liveParams, backendParams, isDefaultFilters } from "./filters";
import { traceFiltersFromNav, navFromTraceFilters, cursorsFromNav, navFromCursors, lookbackLabel } from "./params";
import { LiveSessions, BackendSessions } from "./sessions";
import { TraceHeader, TraceTabs } from "./detail";
import { readNav, writeNav, NAV_EVENT, NavState } from "./nav";
import { useActive } from "./index";
import { Checkbox } from "./sdk";

const POLL_MS = 3000;
const PAGE = 50;
const VIEW_KEY = "hermes_otel.tracesView";

function readView(): "turns" | "sessions" {
  try {
    return localStorage.getItem(VIEW_KEY) === "sessions" ? "sessions" : "turns";
  } catch {
    return "turns";
  }
}

type Page = { rows: TraceRow[]; total: number | null; hasMore: boolean; nextBefore: string | null; ignored: string[] };
const EMPTY_PAGE: Page = { rows: [], total: null, hasMore: false, nextBefore: null, ignored: [] };

/** Shared paging + URL state for a list of traces, whichever source answers. */
function useTracePaging(initialNav: NavState) {
  const [filters, setFilters] = useState<TraceFilters>(() => traceFiltersFromNav(initialNav));
  const [applied, setApplied] = useState<TraceFilters>(() => traceFiltersFromNav(initialNav));
  const [cursors, setCursors] = useState<string[]>(() => cursorsFromNav(initialNav.before));
  const before = cursors.length ? cursors[cursors.length - 1] : null;
  useEffect(() => {
    writeNav({ ...navFromTraceFilters(applied), before: navFromCursors(cursors) });
  }, [applied, cursors]);
  const submit = () => {
    setApplied(filters);
    setCursors([]);
  };
  const older = (next: string | null) => {
    if (next) setCursors((c) => [...c, next]);
  };
  const newer = () => setCursors((c) => c.slice(0, -1));
  const newest = () => setCursors([]);
  return { filters, setFilters, applied, submit, cursors, before, older, newer, newest, page: cursors.length + 1 };
}

// ════════════════════════════════ LIVE SOURCE ════════════════════════════
function LiveTraces({ view, wanted, active }: { view: "turns" | "sessions"; wanted: NavState; active: boolean }) {
  const paging = useTracePaging(wanted);
  const { applied, before } = paging;
  const [page, setPage] = useState<Page>(EMPTY_PAGE);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);
  const [selected, setSelected] = useState<LiveTrace | null>(null);
  const [detailSpans, setDetailSpans] = useState<LiveSpan[] | null>(null);
  const [showPings, setShowPings] = useState(false);
  const [paused, setPaused] = useState(false);
  const inflight = useRef(false);

  const load = useCallback(async () => {
    if (inflight.current) return;
    inflight.current = true;
    try {
      const r = await api("/live/traces", liveParams(applied, PAGE, before));
      const rows: TraceRow[] = (r.traces || []).map(rowFromLive);
      setPage({
        rows,
        total: typeof r.total === "number" ? r.total : null,
        hasMore: !!r.has_more,
        nextBefore: r.next_before_ns != null ? String(r.next_before_ns) : rows.length === PAGE ? String(rows[rows.length - 1].startNs) : null,
        ignored: [],
      });
      setError(null);
    } catch (e: unknown) {
      setError(e);
    } finally {
      inflight.current = false;
      setLoading(false);
    }
  }, [applied, before]);
  useEffect(() => {
    setLoading(true);
    load();
  }, [load]);
  // Only the newest page follows new turns; an older page is a fixed window.
  usePolling(load, POLL_MS, active && !paused && !selected && view === "turns" && !before);

  const loadDetail = useCallback(async (id: string) => {
    const r = await api(`/live/traces/${encodeURIComponent(id)}`);
    return r;
  }, []);
  useEffect(() => {
    if (!selected) return;
    setDetailSpans(null);
    loadDetail(selected.traceId)
      .then((r: any) => {
        setDetailSpans(r.spans || []);
        if (r.trace) setSelected((s) => (s && s.traceId === selected.traceId ? { ...s, ...r.trace } : s));
      })
      .catch(() => setDetailSpans([]));
  }, [selected?.traceId, loadDetail]);
  // A running turn keeps its detail fresh.
  const refreshDetail = useCallback(() => {
    if (!selected) return;
    loadDetail(selected.traceId)
      .then((r: any) => setDetailSpans(r.spans || []))
      .catch(() => undefined);
  }, [selected?.traceId, loadDetail]);
  usePolling(refreshDetail, POLL_MS, active && !!selected && (!!selected.partial || (detailSpans != null && !findRoot(detailSpans))));

  // ?trace=<id> (a pasted link, or the Logs tab) opens that trace (#185).
  useEffect(() => {
    if (!wanted.trace) return;
    loadDetail(wanted.trace)
      .then((r: any) => {
        if (r.trace) setSelected({ ...r.trace, spans: r.spans });
      })
      .catch((e: unknown) => setError(e));
  }, [wanted.trace, loadDetail]);
  useEffect(() => {
    if (selected) writeNav({ trace: String(selected.traceId) });
    else if (!wanted.trace) writeNav({ trace: "" });
  }, [selected, wanted.trace]);

  if (selected) {
    const spans = detailSpans || [];
    const { roots } = liveTreeFromSpans(spans);
    const trace: LiveTrace = { ...selected, spans };
    return (
      <div className="space-y-2">
        <LiveTraceDetail
          trace={trace}
          roots={roots}
          loading={detailSpans === null}
          onBack={() => {
            setSelected(null);
            writeNav({ trace: "" });
          }}
        />
      </div>
    );
  }

  const pingCount = page.rows.filter((t) => isMcpKeepalivePing(t.rootName, t.error)).length;
  const shown = showPings ? page.rows : page.rows.filter((t) => !isMcpKeepalivePing(t.rootName, t.error));
  const oldest = shown.length ? shown[shown.length - 1] : null;
  const newestRow = shown.length ? shown[0] : null;

  return (
    <div className="space-y-3">
      <Card>
        <CardContent className="space-y-3 pt-4">
          <FilterBar
            filters={paging.filters}
            onChange={paging.setFilters}
            onSubmit={paging.submit}
            backend={false}
            busy={loading}
            support={null}
            hide={view === "sessions" ? ["status", "kind", "tool", "model", "minDurationMs", "text", "traceId", "q", "service", "rootsOnly"] : undefined}
          />
        </CardContent>
      </Card>
      {error ? <ErrorBanner error={error} /> : null}
      {view === "sessions" ? (
        <LiveSessions filters={applied} wantedSession={wanted.session || ""} onSelectTrace={setSelected} active={active} />
      ) : (
        <>
          <div className="flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
            <span>
              {shown.length}
              {page.total != null ? ` of ${page.total}` : ""} trace{page.total === 1 ? "" : "s"}
              {isDefaultFilters({ ...applied, lookback: DEFAULT_FILTERS.lookback }) ? "" : " matching"} in the last {lookbackLabel(applied.lookback)}
              {before ? " · older page, not following" : paused ? " · paused" : " · following"}
            </span>
            <Toggle checked={showPings} onChange={setShowPings} label={`show MCP keepalive pings${pingCount ? ` (${pingCount})` : ""}`} Switch={Checkbox} />
            <Button
              variant="outline"
              size="sm"
              className="ml-auto"
              onClick={() => setPaused((p) => !p)}
              disabled={!!before}
              title={before ? "an older page does not follow" : paused ? "resume following new turns" : "stop following new turns"}
            >
              {paused ? <IconPlay size={12} /> : <IconPause size={12} />}
              <span className="ml-1">{paused ? "Resume" : "Pause"}</span>
            </Button>
          </div>
          {shown.length === 0 && !loading ? (
            <Empty title={page.total ? "Nothing matched" : before ? "No older traces" : "No traces yet"}>
              {page.total ? (
                "Widen the lookback or clear a filter."
              ) : before ? (
                <Button variant="outline" size="sm" onClick={paging.newer}>
                  ← Back to the newer page
                </Button>
              ) : (
                "Run a Hermes turn — each turn appears here as a trace you can open into a span waterfall. No backend needed."
              )}
            </Empty>
          ) : (
            <div className="flex flex-col gap-2">
              {shown.map((t) => (
                <TraceCard key={t.traceId} row={t} onSelect={(r) => setSelected(r.raw as LiveTrace)} />
              ))}
            </div>
          )}
          {shown.length || before ? (
            <Pager
              page={paging.page}
              hasMore={page.hasMore}
              onNewest={paging.newest}
              onNewer={paging.newer}
              onOlder={() => paging.older(page.nextBefore)}
              range={
                oldest && newestRow ? `${new Date(oldest.startNs / 1e6).toLocaleTimeString()} → ${new Date(newestRow.startNs / 1e6).toLocaleTimeString()}` : ""
              }
            />
          ) : null}
        </>
      )}
    </div>
  );
}

// ═══════════════════════════════ BACKEND SOURCE ══════════════════════════
function StatusBar({ status, onRefresh }: { status: any; onRefresh: () => void }) {
  if (!status) return null;
  const configured = status.configured;
  const entry = (status.available || []).find((b: any) => b.name === status.active) || null;
  const caps = [configured ? "traces" : null, entry?.metrics || status.metrics ? "metrics" : null, entry?.logs || status.logs ? "logs" : null].filter(Boolean);
  return (
    <div className="flex items-start justify-between gap-3">
      <div className="min-w-0 flex-1 space-y-1.5">
        <div className="flex items-center gap-2">
          <span className={cn("h-2.5 w-2.5 rounded-full", configured ? "otel-pulse-dot" : "bg-muted-foreground/40")} aria-hidden />
          <span className="text-base font-semibold tracking-tight">{configured ? status.name || status.type : "Not configured"}</span>
          {configured && status.type && status.type !== status.name ? (
            <Badge variant="secondary" className="text-[10px] uppercase">
              {status.type}
            </Badge>
          ) : null}
          {caps.map((c) => (
            <Badge key={c} variant="secondary" className="text-[10px]">
              {c}
            </Badge>
          ))}
          {status.query_backend_pin && status.query_backend_pin === status.active ? (
            <span className="text-[10px] text-muted-foreground">default (query_backend)</span>
          ) : null}
          {status.default_service ? (
            <span className="text-[10px] text-muted-foreground" title="service the adapter searches when the service field is empty">
              service {status.default_service}
            </span>
          ) : null}
        </div>
        {configured && status.query_url ? <div className="truncate font-mono text-xs text-muted-foreground">{status.query_url}</div> : null}
      </div>
      <Button variant="outline" size="sm" onClick={onRefresh}>
        Refresh
      </Button>
    </div>
  );
}

function BackendTraceDetail({
  row,
  detail,
  loading,
  error,
  onBack,
  source,
  status,
}: {
  row: TraceRow;
  detail: any;
  loading: boolean;
  error: unknown;
  onBack: () => void;
  source: string;
  status: any;
}) {
  const tree = useMemo(() => (detail ? buildSpanTree(detail.batches || (detail.trace && detail.trace.batches)) : { roots: [], all: [] }), [detail]);
  const rootSpan = tree.roots[0] || null;
  const rootAttrs = rootSpan ? rootSpan._attrs : traceAttrs(row.raw);
  const durationMs = rootSpan ? rootSpan.durationMs : row.durationMs;
  const isError = tree.all.some((s) => (s.status?.code ?? s.status?.statusCode) === 2) || row.error;
  const startNs = tree.all.length ? Math.min(...tree.all.map((s) => s.startNs)) : row.startNs;
  const endNs = tree.all.length ? Math.max(...tree.all.map((s) => s.endNs)) : row.endNs;
  return (
    <Card>
      <CardHeader className="otel-space-y-0">
        <TraceHeader
          title={rootSpan?.name || row.rootName || "—"}
          traceId={row.traceId}
          service={row.service}
          durationMs={durationMs}
          rootAttrs={rootAttrs}
          spans={tree.all.map((s) => ({ name: s.name, attributes: s._attrs }))}
          error={isError}
          truncated={!!detail?.truncated}
          spanCount={detail?.span_count ?? row.spanCount}
          uiUrl={detail?.ui_url || null}
          uiLabel={status?.name || status?.type || null}
          source={source}
          onBack={onBack}
        />
      </CardHeader>
      <CardContent>
        {loading ? <div className="py-8 text-center text-sm text-muted-foreground">Loading trace…</div> : null}
        {error ? <ErrorBanner error={error} prefix="Trace" /> : null}
        {!loading && !error ? (
          <TraceTabs
            traceId={row.traceId}
            source={source}
            logsAvailable={!!status?.logs}
            spans={<SpanTreeView roots={tree.roots} source={source} />}
            raw={detail}
            windowNs={[startNs, endNs]}
          />
        ) : null}
      </CardContent>
    </Card>
  );
}

function BackendTraces({
  status,
  onRefresh,
  source,
  view,
  wanted,
  support,
  active,
}: {
  status: any;
  onRefresh: () => void;
  source: string;
  view: "turns" | "sessions";
  wanted: NavState;
  support: any;
  active: boolean;
}) {
  const paging = useTracePaging(wanted);
  const { applied, before } = paging;
  const [page, setPage] = useState<Page | null>(null);
  const [showPings, setShowPings] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [selected, setSelected] = useState<TraceRow | null>(null);
  const [detail, setDetail] = useState<any>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<unknown>(null);
  const inflight = useRef(false);

  const byId = (id: string): TraceRow => ({
    traceId: id,
    rootName: "(by id)",
    rootKind: "other",
    service: null,
    startNs: 0,
    endNs: 0,
    durationMs: 0,
    spanCount: null,
    model: null,
    tokens: null,
    cost: null,
    error: false,
    session: null,
    partial: false,
    toolName: null,
    inPreview: null,
    outPreview: null,
    raw: { traceID: id },
  });

  const search = useCallback(async () => {
    if (!status?.configured || inflight.current) return;
    // A trace id opens that trace directly: no backend can search by id portably.
    const id = applied.traceId.trim();
    if (id) {
      setSelected(byId(id));
      return;
    }
    inflight.current = true;
    setLoading(true);
    setError(null);
    try {
      const r = await api("/traces/search", backendParams(applied, source, PAGE, before));
      const rows: TraceRow[] = (r.traces || []).map(rowFromBackend);
      setPage({
        rows,
        total: null,
        hasMore: !!r.has_more,
        nextBefore: r.next_before_ns != null ? String(r.next_before_ns) : null,
        ignored: r.ignored_filters || [],
      });
    } catch (e: unknown) {
      setError(e);
      setPage(EMPTY_PAGE);
    } finally {
      inflight.current = false;
      setLoading(false);
    }
  }, [applied, before, status?.configured, source]);

  // Searches run on mount, on submit, on a new page and on a source change (#281).
  useEffect(() => {
    setSelected(null);
    search();
  }, [search]);
  usePolling(search, POLL_MS * 5, active && view === "turns" && !selected && !before && !!status?.configured);

  // ?trace=<id> opens that trace on this backend (#185).
  useEffect(() => {
    if (wanted.trace && status?.configured) setSelected(byId(wanted.trace));
  }, [wanted.trace, status?.configured]);
  useEffect(() => {
    if (selected) writeNav({ trace: selected.traceId });
    else if (!wanted.trace) writeNav({ trace: "" });
  }, [selected, wanted.trace]);

  useEffect(() => {
    if (!selected) return;
    setDetail(null);
    setDetailError(null);
    setDetailLoading(true);
    const p = withBackend(new URLSearchParams(), source);
    api(`/traces/${encodeURIComponent(selected.traceId)}`, p)
      .then(setDetail)
      .catch((e: unknown) => setDetailError(e))
      .finally(() => setDetailLoading(false));
  }, [selected, source]);

  if (!status?.configured)
    return (
      <Card>
        <CardContent className="space-y-2 pt-4 text-sm text-muted-foreground">
          <p>{status?.reason || "No queryable trace backend configured."}</p>
          <p className="text-xs">
            That's fine — the <span className="font-medium text-foreground">Live</span> source needs no backend. Add a backend of a queryable type to browse
            historical traces here: <span className="font-mono">{(status?.queryable_types || []).join(", ") || "none available"}</span>.
          </p>
        </CardContent>
      </Card>
    );

  if (selected)
    return (
      <BackendTraceDetail
        row={selected}
        detail={detail}
        loading={detailLoading}
        error={detailError}
        onBack={() => {
          setSelected(null);
          writeNav({ trace: "" });
        }}
        source={source}
        status={status}
      />
    );

  // Same default as the Live views: successful MCP keepalive pings stay out of
  // the list unless asked for.
  const rows = page?.rows || [];
  const pingCount = rows.filter((t) => isMcpKeepalivePing(t.rootName, t.error)).length;
  const shown = showPings ? rows : rows.filter((t) => !isMcpKeepalivePing(t.rootName, t.error));

  return (
    <div className="space-y-3">
      <Card>
        <CardContent className="space-y-3 pt-4">
          <StatusBar status={status} onRefresh={onRefresh} />
          <FilterBar
            filters={paging.filters}
            onChange={paging.setFilters}
            onSubmit={paging.submit}
            backend
            status={status}
            busy={loading}
            support={support}
            hide={view === "sessions" ? ["traceId"] : undefined}
          />
          <div className="flex flex-wrap items-center justify-between gap-3">
            <span className="text-xs text-muted-foreground">
              {page == null ? "Searching…" : `${shown.length} trace${shown.length === 1 ? "" : "s"} on this page`}
              {page?.ignored.length ? <span title="fields this backend's adapter does not honour"> · ignored: {page.ignored.join(", ")}</span> : null}
            </span>
            <Toggle checked={showPings} onChange={setShowPings} label={`show MCP keepalive pings${pingCount ? ` (${pingCount})` : ""}`} Switch={Checkbox} />
          </div>
        </CardContent>
      </Card>
      {error ? <ErrorBanner error={error} prefix="Search" /> : null}
      {page && shown.length > 0 ? (
        view === "sessions" ? (
          <BackendSessions
            rows={shown}
            wantedSession={wanted.session || ""}
            renderTrace={(t: TraceRow) => <TraceCard key={t.traceId} row={t} onSelect={setSelected} />}
          />
        ) : (
          <div className="flex flex-col gap-2">
            {shown.map((t) => (
              <TraceCard key={t.traceId} row={t} onSelect={setSelected} />
            ))}
          </div>
        )
      ) : page && shown.length === 0 && !error ? (
        <Empty title={before ? "No older traces" : "No traces matched"}>
          {pingCount ? (
            `Only MCP keepalive pings matched (${pingCount} hidden): show them with the switch above.`
          ) : before ? (
            <Button variant="outline" size="sm" onClick={paging.newer}>
              ← Back to the newer page
            </Button>
          ) : (
            "Widen the lookback or run a turn."
          )}
        </Empty>
      ) : null}
      {page && (shown.length || before) ? (
        <Pager page={paging.page} hasMore={page.hasMore} onNewest={paging.newest} onNewer={paging.newer} onOlder={() => paging.older(page.nextBefore)} />
      ) : null}
    </div>
  );
}

// ═══════════════════════════════════ PAGE ════════════════════════════════
export function TracesPage() {
  const { source, setSource, status, refresh, isLive, filters } = useSource();
  const active = useActive();
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
      setNav({ ...readNav(), ...d });
    };
    window.addEventListener(NAV_EVENT, onNav);
    return () => window.removeEventListener(NAV_EVENT, onNav);
  }, [setSource]);
  useEffect(() => {
    if (active) writeNav({ tab: "traces", source: source === "live" ? "" : source, view });
  }, [source, view, active]);

  const key = `${source}:${nav.trace || ""}:${nav.session || ""}`;
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-3">
          <SourceSelect source={source} onChange={setSource} status={status} need="traces" />
          <Segmented
            value={view}
            onChange={setView}
            label="turns or sessions"
            options={[
              { id: "turns", label: "Turns" },
              { id: "sessions", label: "Sessions" },
            ]}
          />
        </div>
        {isLive ? <MiniLabel>queried from the in-process store</MiniLabel> : null}
      </div>
      {isLive ? (
        <LiveTraces key={key} view={view} wanted={nav} active={active} />
      ) : (
        <BackendTraces key={key} status={status} onRefresh={refresh} source={source} view={view} wanted={nav} support={filters} active={active} />
      )}
    </div>
  );
}
