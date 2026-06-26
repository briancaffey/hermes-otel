import {
  React,
  useState,
  useEffect,
  useCallback,
  useMemo,
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
  flatten,
  statusCode,
  kindOf,
  KIND_HEX,
  TreeSpan,
} from "./lib";
import { categorize, IconCoins, IconChevronRight } from "./icons";
import { MiniLabel, ErrorBanner } from "./atoms";

/* eslint-disable @typescript-eslint/no-explicit-any */

// ── status bar (active backend + configured backends) ────────────────────
function StatusBar({ status, onRefresh }: { status: any; onRefresh: () => void }) {
  if (!status) return null;
  const configured = status.configured;
  return (
    <div className="flex items-start justify-between gap-3">
      <div className="min-w-0 flex-1 space-y-1.5">
        <div className="flex items-center gap-2">
          <span className={cn("h-2.5 w-2.5 rounded-full", configured ? "bg-emerald-400" : "bg-muted-foreground/40")} />
          <span className="text-base font-semibold tracking-tight">
            {configured ? status.name || status.type : "Not configured"}
          </span>
          {configured && status.type && status.type !== status.name ? (
            <Badge variant="secondary" className="text-[10px] uppercase">
              {status.type}
            </Badge>
          ) : null}
        </div>
        {configured && status.query_url ? (
          <div className="truncate font-mono text-xs text-muted-foreground">{status.query_url}</div>
        ) : null}
        {status.backends && status.backends.length ? (
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
      <Button variant="outline" size="sm" onClick={onRefresh}>
        Refresh
      </Button>
    </div>
  );
}

function BackendEmpty({ status }: { status: any }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Traces (backend query)</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <p className="text-sm text-muted-foreground">{status ? status.reason : "Loading…"}</p>
        {status && status.backends && status.backends.length ? (
          <div className="space-y-2">
            <MiniLabel>Configured backends</MiniLabel>
            <ul className="space-y-1">
              {status.backends.map((b: any, i: number) => (
                <li key={i} className="flex items-center gap-2 text-sm">
                  <Badge variant={b.supported ? "default" : "secondary"}>{b.type}</Badge>
                  <span className="font-mono text-xs text-muted-foreground">{b.endpoint || "(no endpoint)"}</span>
                  <span className="text-xs text-muted-foreground">
                    {b.supported ? "queryable" : "view-only in its own UI"}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        ) : null}
        <p className="text-xs text-muted-foreground">
          No backend? The <span className="font-medium text-foreground">⚡ Live</span> tab needs none — it streams the
          agent's telemetry from the plugin's in-process store.
        </p>
      </CardContent>
    </Card>
  );
}

// ── filters ──────────────────────────────────────────────────────────────
function FiltersForm({
  filters,
  status,
  onChange,
  onSubmit,
}: {
  filters: any;
  status: any;
  onChange: (f: any) => void;
  onSubmit: () => void;
}) {
  const rawLabel = status?.query_lang_label ? `${status.query_lang_label} filter (optional)` : "Query filter (optional)";
  const set = (k: string, v: any) => onChange({ ...filters, [k]: v });
  return (
    <form
      className="grid items-end gap-3 md:grid-cols-[minmax(0,2fr)_minmax(0,1fr)_120px_150px_120px]"
      onSubmit={(e: any) => {
        e.preventDefault();
        onSubmit();
      }}
    >
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
      <div className="space-y-1.5">
        <Label htmlFor="otel-match">Match</Label>
        <Select id="otel-match" value={filters.rootsOnly ? "root" : "any"} onValueChange={(v: string) => set("rootsOnly", v === "root")}>
          <SelectOption value="root">Root span only</SelectOption>
          <SelectOption value="any">Any span</SelectOption>
        </Select>
      </div>
      <button type="submit" className="hidden" tabIndex={-1} aria-hidden />
    </form>
  );
}

// ── trace card ─────────────────────────────────────────────────────────-─
function TraceCard({ trace, onSelect }: { trace: any; onSelect: (t: any) => void }) {
  const cat = categorize(trace.rootTraceName || "");
  const attrs = traceAttrs(trace);
  const startNs = trace.startTimeUnixNano ? Number(trace.startTimeUnixNano) : 0;
  const model = attrs["llm.model_name"] || attrs["gen_ai.response.model"];
  const provider = attrs["llm.provider"] || attrs["gen_ai.provider.name"];
  const toolName = attrs["tool.name"];
  const totalTokens = attrs["gen_ai.usage.total_tokens"] || attrs["llm.token_count.total"];
  const inTok = attrs["gen_ai.usage.input_tokens"];
  const outTok = attrs["gen_ai.usage.output_tokens"];
  const cost = attrs["hermes.cost.usage"];
  const isError = attrs["status"] === "error" || attrs["error.type"];
  const inputRaw = extractInputPreview(attrs);
  const outputRaw = extractOutputPreview(attrs);
  const inP = clip(inputRaw, 140);
  const outP = clip(outputRaw, 140);
  const spanCount = traceSpanCount(trace);

  const chips: { key: string; label: string; variant: string; mono?: boolean }[] = [];
  if (toolName) chips.push({ key: "tool", label: String(toolName), variant: "secondary", mono: true });
  if (model) chips.push({ key: "model", label: String(model), variant: "secondary", mono: true });
  if (provider) chips.push({ key: "prov", label: String(provider), variant: "outline" });

  const meta: any[] = [];
  const pushMeta = (key: string, node: any) => {
    if (node == null) return;
    if (meta.length) meta.push(<span key={key + "-s"} className="text-border">·</span>);
    meta.push(<span key={key}>{node}</span>);
  };
  pushMeta("svc", trace.rootServiceName || null);
  pushMeta("dur", <span className="tabular-nums">{fmtDurationMs(trace.durationMs)}</span>);
  if (spanCount != null) pushMeta("sp", <span className="tabular-nums">{spanCount} span{spanCount === 1 ? "" : "s"}</span>);
  if (totalTokens != null)
    pushMeta(
      "tok",
      <span className="inline-flex items-center gap-1 tabular-nums" title={inTok != null && outTok != null ? `${inTok} in / ${outTok} out` : undefined}>
        <IconCoins size={12} className="opacity-70" />
        {fmtTokens(totalTokens)} tok
      </span>
    );
  if (cost) pushMeta("cost", <span className="tabular-nums text-emerald-400">${Number(cost).toFixed(4)}</span>);
  pushMeta("time", <span title={fmtAbsTime(startNs)}>{fmtTimeAgo(startNs)}</span>);

  const Icon = cat.Icon;
  return (
    <div
      className={cn(
        "group relative cursor-pointer overflow-hidden border bg-card/40 transition-colors hover:bg-secondary/30 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring",
        isError ? "border-destructive/30 bg-destructive/[0.04]" : "border-border"
      )}
      role="button"
      tabIndex={0}
      title={trace.traceID || trace.traceId}
      onClick={() => onSelect(trace)}
      onKeyDown={(e: any) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onSelect(trace);
        }
      }}
    >
      <div className="flex items-start gap-3 p-3">
        <div className={cn("shrink-0 pt-0.5", cat.color)}>
          <Icon size={16} />
        </div>
        <div className="min-w-0 flex-1 space-y-1">
          <div className="flex min-w-0 items-center gap-2">
            <span className="truncate font-mono text-sm">{trace.rootTraceName || "—"}</span>
            {cat.label ? <Badge variant="secondary" className="shrink-0 text-[10px]">{cat.label}</Badge> : null}
            {isError ? <Badge variant="destructive" className="shrink-0 text-[10px]">error</Badge> : null}
          </div>
          {chips.length ? (
            <div className="flex flex-wrap items-center gap-1">
              {chips.map((c) => (
                <Badge key={c.key} variant={c.variant} className={cn("text-[10px]", c.mono ? "font-mono" : null)}>
                  {c.label}
                </Badge>
              ))}
            </div>
          ) : null}
          {inP || outP ? (
            <div className="space-y-0.5 border-l-2 border-border/60 pl-2 text-xs">
              {inP ? (
                <div className="flex items-baseline gap-2" title={inputRaw || undefined}>
                  <span className="w-6 shrink-0 text-[10px] font-medium text-muted-foreground">in</span>
                  <span className="truncate text-foreground/80">{inP}</span>
                </div>
              ) : null}
              {outP ? (
                <div className="flex items-baseline gap-2" title={outputRaw || undefined}>
                  <span className="w-6 shrink-0 text-[10px] font-medium text-muted-foreground">out</span>
                  <span className="truncate text-foreground/80">{outP}</span>
                </div>
              ) : null}
            </div>
          ) : null}
          <div className="flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">{meta}</div>
          <div className="truncate font-mono text-[10px] text-muted-foreground/60">{trace.traceID || trace.traceId}</div>
        </div>
        <div className="shrink-0 self-center text-muted-foreground opacity-30 transition-opacity group-hover:opacity-90">
          <IconChevronRight size={16} />
        </div>
      </div>
    </div>
  );
}

// ── attribute table + span row (with waterfall bar) ──────────────────────
function AttrTable({ attrs }: { attrs: Record<string, any> }) {
  const keys = Object.keys(attrs || {}).sort();
  if (!keys.length) return <div className="text-xs text-muted-foreground">(no attributes)</div>;
  return (
    <dl className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1 text-xs">
      {keys.map((k) => {
        const v = attrs[k];
        const rendered = v && typeof v === "object" ? JSON.stringify(v, null, 2) : String(v);
        return (
          <React.Fragment key={k}>
            <dt className="text-muted-foreground">{k}</dt>
            <dd className="whitespace-pre-wrap break-words text-foreground">{rendered}</dd>
          </React.Fragment>
        );
      })}
    </dl>
  );
}

function SpanRow({
  span,
  depth,
  open,
  onToggle,
  t0,
  total,
}: {
  span: TreeSpan;
  depth: number;
  open: boolean;
  onToggle: () => void;
  t0: number;
  total: number;
}) {
  const kind = kindOf(span.name, span._attrs);
  const isErr = statusCode(span.status) === "error";
  const left = total ? ((span.startNs - t0) / total) * 100 : 0;
  const width = total ? Math.max(0.6, (span.durationMs * 1e6 * 100) / total) : 0;
  const cost = span._attrs["hermes.cost.usage"];
  const tokens = span._attrs["gen_ai.usage.total_tokens"] || span._attrs["llm.token_count.total"];
  const approval = span._attrs["hermes.approval.choice"];
  return (
    <li className="border-b border-border last:border-b-0">
      <div className="flex cursor-pointer items-center gap-2 px-3 py-1.5 hover:bg-accent/30" style={{ paddingLeft: 12 + depth * 16 }} onClick={onToggle}>
        <span className="w-3 text-xs text-muted-foreground">{open ? "▾" : "▸"}</span>
        <span className="inline-block h-2 w-2 shrink-0 rounded-full" style={{ background: KIND_HEX[kind] }} />
        <span className="truncate font-mono text-sm">{span.name}</span>
        {isErr ? <Badge variant="destructive" className="text-[10px]">error</Badge> : null}
        {approval ? <Badge variant="secondary" className="text-[10px]">👤 {approval}</Badge> : null}
        {/* waterfall timing track */}
        <div className="relative ml-2 hidden h-3 min-w-[80px] flex-1 bg-muted/30 sm:block">
          <div
            className="absolute top-0.5 h-2"
            style={{ left: `${left}%`, width: `${width}%`, background: KIND_HEX[kind], minWidth: 2 }}
            title={`${span.name} · ${fmtDurationMs(span.durationMs)}`}
          />
        </div>
        {tokens ? <span className="hidden tabular-nums text-[11px] text-muted-foreground md:inline">{fmtTokens(tokens)} tok</span> : null}
        {cost ? <span className="hidden tabular-nums text-[11px] text-emerald-400 md:inline">${Number(cost).toFixed(4)}</span> : null}
        <span className="ml-auto shrink-0 tabular-nums text-xs text-muted-foreground">{fmtDurationMs(span.durationMs)}</span>
      </div>
      {open ? (
        <div className="bg-muted/20 px-4 py-3" style={{ paddingLeft: 28 + depth * 16 }}>
          <AttrTable attrs={span._attrs} />
        </div>
      ) : null}
    </li>
  );
}

function TraceDetail({
  trace,
  detail,
  loading,
  error,
  onBack,
}: {
  trace: any;
  detail: any;
  loading: boolean;
  error: string | null;
  onBack: () => void;
}) {
  const [openIds, setOpenIds] = useState<Record<string, boolean>>({});
  const tree = useMemo(() => (detail ? buildSpanTree(detail.batches || (detail.trace && detail.trace.batches)) : null), [detail]);
  const flat = useMemo(() => (tree ? flatten(tree.roots) : []), [tree]);
  const t0 = flat.length ? Math.min(...flat.map((n) => n.span.startNs)) : 0;
  const total = flat.length ? Math.max(...flat.map((n) => n.span.endNs)) - t0 || 1 : 1;

  const toggle = (id: string) => setOpenIds((p) => ({ ...p, [id]: !p[id] }));
  const expandAll = () => setOpenIds(Object.fromEntries(flat.map((n) => [n.span.spanId, true])));
  const collapseAll = () => setOpenIds({});

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
        <div className="flex shrink-0 gap-2">
          <Button variant="outline" size="sm" onClick={expandAll} disabled={!flat.length}>
            Expand all
          </Button>
          <Button variant="outline" size="sm" onClick={collapseAll} disabled={!flat.length}>
            Collapse
          </Button>
          <Button variant="ghost" size="sm" onClick={onBack}>
            ← Back
          </Button>
        </div>
      </CardHeader>
      <CardContent>
        {loading ? <div className="py-8 text-center text-sm text-muted-foreground">Loading trace…</div> : null}
        {error ? <ErrorBanner error={error} /> : null}
        {!loading && !error && !flat.length ? (
          <div className="py-8 text-center text-sm text-muted-foreground">No spans returned for this trace.</div>
        ) : null}
        {!loading && !error && flat.length ? (
          <ul className="overflow-hidden border border-border">
            {flat.map((n) => (
              <SpanRow
                key={n.span.spanId}
                span={n.span}
                depth={n.depth}
                open={!!openIds[n.span.spanId]}
                onToggle={() => toggle(n.span.spanId)}
                t0={t0}
                total={total}
              />
            ))}
          </ul>
        ) : null}
      </CardContent>
    </Card>
  );
}

// ── page ───────────────────────────────────────────────────────────────-─
const PAGE = 10;
export function TracesPage() {
  const [status, setStatus] = useState<any>(null);
  const [statusErr, setStatusErr] = useState<string | null>(null);
  const [filters, setFilters] = useState<any>({ lookback: 1, q: "", service: "", rootsOnly: true });
  const [traces, setTraces] = useState<any[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<any>(null);
  const [detail, setDetail] = useState<any>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [page, setPage] = useState(0);

  const loadStatus = useCallback(() => {
    setStatusErr(null);
    fetchJSON(`${API}/status`)
      .then(setStatus)
      .catch((e: any) => setStatusErr(String(e?.message || e)));
  }, []);
  useEffect(() => loadStatus(), [loadStatus]);

  const search = useCallback(async () => {
    if (!status?.configured) return;
    setLoading(true);
    setError(null);
    setSelected(null);
    setPage(0);
    try {
      const p = new URLSearchParams({ limit: "50", lookback_hours: String(filters.lookback), roots_only: String(filters.rootsOnly) });
      if (filters.q?.trim()) p.set("q", filters.q.trim());
      if (filters.service?.trim()) p.set("service", filters.service.trim());
      const r = await fetchJSON(`${API}/traces/search?${p}`);
      setTraces(r.traces || []);
    } catch (e: any) {
      setError(String(e?.message || e));
      setTraces([]);
    } finally {
      setLoading(false);
    }
  }, [filters, status]);
  useEffect(() => {
    if (status?.configured) search();
  }, [status]); // eslint-disable-line

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

  if (statusErr) return <ErrorBanner error={"Failed to load backend status: " + statusErr} />;
  if (status && !status.configured) return <BackendEmpty status={status} />;

  const pageItems = (traces || []).slice(page * PAGE, page * PAGE + PAGE);
  const pages = Math.ceil((traces || []).length / PAGE);

  return (
    <div className="space-y-3">
      <Card>
        <CardContent className="space-y-3 pt-4">
          <StatusBar status={status} onRefresh={loadStatus} />
          <FiltersForm filters={filters} status={status} onChange={setFilters} onSubmit={search} />
          <div className="flex items-center justify-between">
            <span className="text-xs text-muted-foreground">
              {traces == null ? "" : `${traces.length} trace${traces.length === 1 ? "" : "s"}`}
            </span>
            <Button onClick={search} disabled={loading} size="sm">
              {loading ? "Searching…" : "Search"}
            </Button>
          </div>
        </CardContent>
      </Card>

      {error ? <ErrorBanner error={error} /> : null}

      {selected ? (
        <TraceDetail trace={selected} detail={detail} loading={detailLoading} error={detailError} onBack={() => setSelected(null)} />
      ) : traces == null ? (
        <div className="py-8 text-center text-sm text-muted-foreground">Loading…</div>
      ) : traces.length === 0 ? (
        <div className="border border-dashed border-border px-4 py-8 text-center text-sm text-muted-foreground">
          No traces matched. Widen the lookback, or run a Hermes turn and Search.
        </div>
      ) : (
        <div className="space-y-2">
          <div className="flex flex-col gap-2">
            {pageItems.map((t) => (
              <TraceCard key={t.traceID || t.traceId} trace={t} onSelect={setSelected} />
            ))}
          </div>
          {pages > 1 ? (
            <div className="flex items-center justify-center gap-3 pt-1 text-xs text-muted-foreground">
              <Button variant="outline" size="sm" disabled={page === 0} onClick={() => setPage((p) => p - 1)}>
                ← Prev
              </Button>
              <span>
                {page + 1} / {pages}
              </span>
              <Button variant="outline" size="sm" disabled={page >= pages - 1} onClick={() => setPage((p) => p + 1)}>
                Next →
              </Button>
            </div>
          ) : null}
        </div>
      )}
    </div>
  );
}
