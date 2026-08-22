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
  CardTitle,
  CardContent,
  Badge,
  Button,
  Input,
  Label,
  Select,
  SelectOption,
  cn,
} from "./sdk";
import {
  fmtDurationMs,
  fmtAbsTime,
  fmtTimeAgo,
  fmtTokens,
  clip,
  traceAttrs,
  traceSpanCount,
  extractInputPreview,
  extractOutputPreview,
  buildSpanTree,
  groupLiveTraces,
  liveTreeFromSpans,
  LiveSpan,
  LiveTrace,
} from "./lib";
import { categorize, IconCoins, IconChevronRight } from "./icons";
import { MiniLabel, ErrorBanner } from "./atoms";
import { SpanTreeView, LiveTraceCard, LiveTraceDetail } from "./spantree";

/* eslint-disable @typescript-eslint/no-explicit-any */
const POLL_MS = 1500;
const MAX_KEEP = 1500;

// ── source toggle ──────────────────────────────────────────────────────-─
function SourceToggle({ source, onChange, backendOk }: { source: string; onChange: (s: string) => void; backendOk: boolean }) {
  const Btn = ({ id, label }: { id: string; label: string }) => (
    <button
      onClick={() => onChange(id)}
      className={cn(
        "border px-3 py-1.5 text-xs font-medium transition-colors",
        source === id ? "border-border bg-accent text-foreground" : "border-transparent text-muted-foreground hover:text-foreground"
      )}
    >
      {label}
    </button>
  );
  return (
    <div className="inline-flex border border-border bg-card/40 p-0.5">
      <Btn id="live" label="⚡ Live (in-process)" />
      <Btn id="backend" label={backendOk ? "🗄 Backend" : "🗄 Backend (offline)"} />
    </div>
  );
}

// ════════════════════════════════ LIVE SOURCE ════════════════════════════
function LiveTraces() {
  const [spans, setSpans] = useState<LiveSpan[]>([]);
  const [selected, setSelected] = useState<LiveTrace | null>(null);
  const [text, setText] = useState("");
  const [errorsOnly, setErrorsOnly] = useState(false);
  const [paused, setPaused] = useState(false);
  const cursor = useRef(0);

  const poll = useCallback(async () => {
    try {
      const r = await fetchJSON(`${API}/live/spans?since=${cursor.current}&limit=2000`);
      cursor.current = Math.max(r.cursor || 0, cursor.current);
      if (r.spans?.length) setSpans((prev) => [...prev, ...r.spans].slice(-MAX_KEEP));
    } catch {
      /* keep last */
    }
  }, []);
  useEffect(() => {
    poll();
  }, [poll]);
  useEffect(() => {
    if (paused || selected) return;
    const id = setInterval(poll, POLL_MS);
    return () => clearInterval(id);
  }, [poll, paused, selected]);

  let traces = groupLiveTraces(spans);
  const f = text.trim().toLowerCase();
  if (f) traces = traces.filter((t) => t.rootName.toLowerCase().includes(f) || (t.model || "").toLowerCase().includes(f));
  if (errorsOnly) traces = traces.filter((t) => t.error);

  if (selected) {
    const fresh = groupLiveTraces(spans).find((t) => t.traceId === selected.traceId) || selected;
    const { roots } = liveTreeFromSpans(fresh.spans);
    return <LiveTraceDetail trace={fresh} roots={roots} onBack={() => setSelected(null)} />;
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <Input placeholder="filter by name / model…" value={text} onChange={(e: any) => setText(e.target.value)} className="h-8 w-56" />
        <Button variant={errorsOnly ? "default" : "outline"} size="sm" onClick={() => setErrorsOnly((v) => !v)}>
          errors only
        </Button>
        <Button variant="outline" size="sm" onClick={() => setPaused((p) => !p)}>
          {paused ? "▶ Resume" : "⏸ Pause"}
        </Button>
        <span className="ml-auto text-xs text-muted-foreground">
          {traces.length} trace{traces.length === 1 ? "" : "s"} · {spans.length} spans buffered
        </span>
      </div>
      {traces.length === 0 ? (
        <div className="border border-dashed border-border px-4 py-12 text-center text-sm text-muted-foreground">
          <div className="mb-1 text-base font-medium text-foreground">No traces yet</div>
          Run a Hermes turn — each turn appears here as a trace you can open into a span waterfall. No backend needed.
        </div>
      ) : (
        <div className="flex flex-col gap-2">
          {traces.map((t) => (
            <LiveTraceCard key={t.traceId} trace={t} onSelect={setSelected} />
          ))}
        </div>
      )}
    </div>
  );
}

// ═══════════════════════════════ BACKEND SOURCE ══════════════════════════
function StatusBar({ status, onRefresh }: { status: any; onRefresh: () => void }) {
  if (!status) return null;
  const configured = status.configured;
  return (
    <div className="flex items-start justify-between gap-3">
      <div className="min-w-0 flex-1 space-y-1.5">
        <div className="flex items-center gap-2">
          <span className={cn("h-2.5 w-2.5 rounded-full", configured ? "bg-emerald-400" : "bg-muted-foreground/40")} />
          <span className="text-base font-semibold tracking-tight">{configured ? status.name || status.type : "Not configured"}</span>
          {configured && status.type && status.type !== status.name ? <Badge variant="secondary" className="text-[10px] uppercase">{status.type}</Badge> : null}
        </div>
        {configured && status.query_url ? <div className="truncate font-mono text-xs text-muted-foreground">{status.query_url}</div> : null}
        {status.backends?.length ? (
          <div className="flex flex-wrap items-center gap-1.5 pt-0.5">
            {status.backends.map((b: any, i: number) => (
              <Badge key={i} variant={b.supported ? "default" : "secondary"} className="text-[10px]">
                {b.name}
                {b.supported ? "" : " · read-only"}
              </Badge>
            ))}
          </div>
        ) : null}
      </div>
      <Button variant="outline" size="sm" onClick={onRefresh}>Refresh</Button>
    </div>
  );
}

function FiltersForm({ filters, status, onChange, onSubmit }: { filters: any; status: any; onChange: (f: any) => void; onSubmit: () => void }) {
  const rawLabel = status?.query_lang_label ? `${status.query_lang_label} filter (optional)` : "Query filter (optional)";
  const set = (k: string, v: any) => onChange({ ...filters, [k]: v });
  return (
    <form className="grid items-end gap-3 md:grid-cols-[minmax(0,2fr)_minmax(0,1fr)_120px_150px]" onSubmit={(e: any) => { e.preventDefault(); onSubmit(); }}>
      <div className="space-y-1.5 md:col-span-2">
        <Label htmlFor="otel-q">{rawLabel}</Label>
        <Input id="otel-q" placeholder={status?.raw_placeholder || ""} value={filters.q} onChange={(e: any) => set("q", e.target.value)} />
      </div>
      <div className="space-y-1.5">
        <Label htmlFor="otel-svc">Service</Label>
        <Input id="otel-svc" placeholder="any" value={filters.service || ""} onChange={(e: any) => set("service", e.target.value)} />
      </div>
      <div className="space-y-1.5">
        <Label htmlFor="otel-lb">Lookback</Label>
        <Select id="otel-lb" value={String(filters.lookback)} onValueChange={(v: string) => set("lookback", Number(v))}>
          <SelectOption value="0.25">15m</SelectOption>
          <SelectOption value="1">1h</SelectOption>
          <SelectOption value="6">6h</SelectOption>
          <SelectOption value="24">24h</SelectOption>
          <SelectOption value="72">3d</SelectOption>
          <SelectOption value="168">7d</SelectOption>
        </Select>
      </div>
      <button type="submit" className="hidden" tabIndex={-1} aria-hidden />
    </form>
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
      className={cn("group flex cursor-pointer items-start gap-3 border bg-card/40 p-3 transition-colors hover:bg-secondary/30", isError ? "border-destructive/30" : "border-border")}
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
          <div className="space-y-0.5 border-l-2 border-border/60 pl-2 text-xs">
            {inP ? <div className="truncate text-foreground/80"><span className="mr-2 text-[10px] text-muted-foreground">in</span>{inP}</div> : null}
            {outP ? <div className="truncate text-foreground/80"><span className="mr-2 text-[10px] text-muted-foreground">out</span>{outP}</div> : null}
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
      <div className="shrink-0 self-center text-muted-foreground opacity-30 group-hover:opacity-90"><IconChevronRight size={16} /></div>
    </div>
  );
}

function BackendTraceDetail({ trace, detail, loading, error, onBack }: { trace: any; detail: any; loading: boolean; error: string | null; onBack: () => void }) {
  const roots = detail ? buildSpanTree(detail.batches || (detail.trace && detail.trace.batches)).roots : [];
  return (
    <Card>
      <CardHeader className="flex flex-row items-start justify-between gap-3 space-y-0">
        <div className="min-w-0 space-y-1">
          <CardTitle className="truncate">{trace.rootTraceName || "—"}</CardTitle>
          <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
            <span>{trace.rootServiceName || "—"}</span>
            <span>·</span>
            <span className="font-mono">{trace.traceID || trace.traceId}</span>
            <span>·</span>
            <span>{fmtDurationMs(trace.durationMs)}</span>
          </div>
        </div>
        <Button variant="ghost" size="sm" onClick={onBack}>← Back</Button>
      </CardHeader>
      <CardContent>
        {loading ? <div className="py-8 text-center text-sm text-muted-foreground">Loading trace…</div> : null}
        {error ? <ErrorBanner error={error} /> : null}
        {!loading && !error ? <SpanTreeView roots={roots} /> : null}
      </CardContent>
    </Card>
  );
}

function BackendTraces({ status, onRefresh }: { status: any; onRefresh: () => void }) {
  const [filters, setFilters] = useState<any>({ lookback: 1, q: "", service: "", rootsOnly: true });
  const [traces, setTraces] = useState<any[] | null>(null);
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
    try {
      const p = new URLSearchParams({ limit: "50", lookback_hours: String(filters.lookback), roots_only: String(filters.rootsOnly) });
      if (filters.q?.trim()) p.set("q", filters.q.trim());
      if (filters.service?.trim()) p.set("service", filters.service.trim());
      const r = await fetchJSON(`${API}/traces/search?${p}`);
      setTraces(r.traces || []);
    } catch (e: any) {
      setError(String(e?.message || e).replace(/^.*?:\s*/, ""));
      setTraces([]);
    } finally {
      setLoading(false);
    }
  }, [filters, status]);

  useEffect(() => {
    if (!selected) return;
    setDetail(null);
    setDetailError(null);
    setDetailLoading(true);
    fetchJSON(`${API}/traces/${selected.traceID || selected.traceId}`)
      .then(setDetail)
      .catch((e: any) => setDetailError(String(e?.message || e)))
      .finally(() => setDetailLoading(false));
  }, [selected]);

  if (!status?.configured)
    return (
      <Card>
        <CardContent className="space-y-2 pt-4 text-sm text-muted-foreground">
          <p>{status?.reason || "No queryable trace backend configured."}</p>
          <p className="text-xs">
            That's fine — the <span className="font-medium text-foreground">⚡ Live</span> source above needs no backend.
            Add an <span className="font-mono">lgtm</span>/<span className="font-mono">tempo</span> backend to browse
            historical traces here.
          </p>
        </CardContent>
      </Card>
    );

  if (selected) return <BackendTraceDetail trace={selected} detail={detail} loading={detailLoading} error={detailError} onBack={() => setSelected(null)} />;

  return (
    <div className="space-y-3">
      <Card>
        <CardContent className="space-y-3 pt-4">
          <StatusBar status={status} onRefresh={onRefresh} />
          <FiltersForm filters={filters} status={status} onChange={setFilters} onSubmit={search} />
          <div className="flex items-center justify-between">
            <span className="text-xs text-muted-foreground">{traces == null ? "Not searched yet" : `${traces.length} trace${traces.length === 1 ? "" : "s"}`}</span>
            <Button onClick={search} disabled={loading} size="sm">{loading ? "Searching…" : "Search"}</Button>
          </div>
        </CardContent>
      </Card>
      {error ? (
        <div className="space-y-1">
          <ErrorBanner error={`Backend query failed: ${error}`} />
          <p className="px-1 text-xs text-muted-foreground">Backend unreachable from the dashboard. Use the ⚡ Live source — it reads the in-process store and always works.</p>
        </div>
      ) : null}
      {traces && traces.length > 0 ? (
        <div className="flex flex-col gap-2">
          {traces.map((t) => (
            <BackendTraceCard key={t.traceID || t.traceId} trace={t} onSelect={setSelected} />
          ))}
        </div>
      ) : traces && traces.length === 0 && !error ? (
        <div className="border border-dashed border-border px-4 py-8 text-center text-sm text-muted-foreground">No traces matched — widen the lookback or run a turn.</div>
      ) : null}
    </div>
  );
}

// ═══════════════════════════════════ PAGE ════════════════════════════════
export function TracesPage() {
  const [source, setSource] = useState("live");
  const [status, setStatus] = useState<any>(null);

  const loadStatus = useCallback(() => {
    fetchJSON(`${API}/status`).then(setStatus).catch(() => setStatus({ configured: false, reason: "status unavailable" }));
  }, []);
  useEffect(() => loadStatus(), [loadStatus]);

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between gap-2">
        <SourceToggle source={source} onChange={setSource} backendOk={!!status?.configured} />
        {source === "live" ? <MiniLabel>traces assembled from the in-process store</MiniLabel> : null}
      </div>
      {source === "live" ? <LiveTraces /> : <BackendTraces status={status} onRefresh={loadStatus} />}
    </div>
  );
}
