import {
  React,
  useState,
  useEffect,
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
} from "./sdk";
import { fmtDuration, spanKind, KIND_COLOR, fmtAgo } from "./lib";
import { EmptyState, ErrorBanner, KindDot } from "./ui";

// Decode an OTLP AnyValue → primitive.
function decodeAttr(v: any): any {
  if (v == null) return null;
  if (typeof v !== "object") return v;
  if ("stringValue" in v) return v.stringValue;
  if ("intValue" in v) return Number(v.intValue);
  if ("doubleValue" in v) return v.doubleValue;
  if ("boolValue" in v) return v.boolValue;
  if ("arrayValue" in v) return (v.arrayValue.values || []).map(decodeAttr);
  return JSON.stringify(v);
}
function attrsToObj(list: any[]): Record<string, any> {
  const o: Record<string, any> = {};
  for (const a of list || []) o[a.key] = decodeAttr(a.value);
  return o;
}

type FlatSpan = {
  spanId: string;
  parentId: string | null;
  name: string;
  startNs: number;
  endNs: number;
  durationMs: number;
  status: string;
  attributes: Record<string, any>;
  depth: number;
};

function flattenTrace(detail: any): FlatSpan[] {
  const spans: FlatSpan[] = [];
  for (const batch of detail.batches || []) {
    for (const ss of batch.scopeSpans || batch.scope_spans || []) {
      for (const sp of ss.spans || []) {
        const start = Number(sp.startTimeUnixNano || sp.start_time_unix_nano || 0);
        const end = Number(sp.endTimeUnixNano || sp.end_time_unix_nano || 0);
        const code = sp.status?.code ?? sp.status?.statusCode;
        spans.push({
          spanId: sp.spanId || sp.span_id,
          parentId: sp.parentSpanId || sp.parent_span_id || null,
          name: sp.name,
          startNs: start,
          endNs: end,
          durationMs: end && start ? (end - start) / 1e6 : 0,
          status: code === 2 || code === "STATUS_CODE_ERROR" ? "ERROR" : "OK",
          attributes: attrsToObj(sp.attributes),
          depth: 0,
        });
      }
    }
  }
  // order by parent → child (depth-first), compute depth
  const byParent: Record<string, FlatSpan[]> = {};
  const ids = new Set(spans.map((s) => s.spanId));
  for (const s of spans) {
    const key = s.parentId && ids.has(s.parentId) ? s.parentId : "__root__";
    (byParent[key] = byParent[key] || []).push(s);
  }
  for (const k of Object.keys(byParent)) byParent[k].sort((a, b) => a.startNs - b.startNs);
  const out: FlatSpan[] = [];
  const walk = (key: string, depth: number) => {
    for (const s of byParent[key] || []) {
      s.depth = depth;
      out.push(s);
      walk(s.spanId, depth + 1);
    }
  };
  walk("__root__", 0);
  return out;
}

function TraceDetail({ traceId }: { traceId: string }) {
  const [spans, setSpans] = useState<FlatSpan[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    setSpans(null);
    setError(null);
    fetchJSON(`${API}/traces/${traceId}`)
      .then((d) => live && setSpans(flattenTrace(d)))
      .catch((e: any) => live && setError(String(e?.message || e)));
    return () => {
      live = false;
    };
  }, [traceId]);

  if (error) return <ErrorBanner error={error} />;
  if (!spans) return <div className="otel-loading">Loading trace…</div>;
  if (!spans.length) return <EmptyState title="No spans in this trace" />;

  const t0 = spans[0].startNs;
  const total = Math.max(...spans.map((s) => s.endNs)) - t0 || 1;

  return (
    <div className="otel-trace-detail">
      {spans.map((s) => {
        const kind = spanKind(s);
        const left = ((s.startNs - t0) / total) * 100;
        const width = Math.max(0.5, (s.durationMs * 1e6 * 100) / total);
        return (
          <div className="otel-wf-row" key={s.spanId} style={{ paddingLeft: 8 + s.depth * 14 }}>
            <div className="otel-wf-name">
              <KindDot kind={kind} />
              <span title={s.name}>{s.name}</span>
              {s.status === "ERROR" ? <Badge variant="destructive">err</Badge> : null}
            </div>
            <div className="otel-wf-track">
              <div
                className="otel-wf-bar"
                style={{ left: `${left}%`, width: `${width}%`, background: KIND_COLOR[kind] }}
                title={`${s.name} · ${fmtDuration(s.durationMs)}`}
              />
            </div>
            <div className="otel-wf-dur">{fmtDuration(s.durationMs)}</div>
          </div>
        );
      })}
    </div>
  );
}

export function TracesPage() {
  const [status, setStatus] = useState<any>(null);
  const [statusErr, setStatusErr] = useState<string | null>(null);
  const [results, setResults] = useState<any[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [filters, setFilters] = useState({ lookback: "1", status: "", free: "", roots: true });

  useEffect(() => {
    fetchJSON(`${API}/status`)
      .then(setStatus)
      .catch((e: any) => setStatusErr(String(e?.message || e)));
  }, []);

  const search = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const p = new URLSearchParams({
        lookback_hours: filters.lookback,
        status: filters.status,
        free_text: filters.free,
        roots_only: String(filters.roots),
        limit: "50",
      });
      const r = await fetchJSON(`${API}/traces/search?${p.toString()}`);
      setResults(r.traces || []);
    } catch (e: any) {
      setError(String(e?.message || e));
      setResults([]);
    } finally {
      setLoading(false);
    }
  }, [filters]);

  useEffect(() => {
    if (status && status.configured) search();
  }, [status]); // eslint-disable-line

  if (statusErr) return <ErrorBanner error={"Failed to load backend status: " + statusErr} />;
  if (status && !status.configured) {
    return (
      <EmptyState
        title="No query backend configured"
        hint={
          (status.reason || "") +
          " — or just use the Live tab, which needs no backend."
        }
      />
    );
  }

  return (
    <div className="otel-traces">
      <Card>
        <CardHeader>
          <CardTitle>Search traces {status?.backend ? `· ${status.backend}` : ""}</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="otel-filters">
            <div>
              <Label>Lookback (h)</Label>
              <Input
                value={filters.lookback}
                onChange={(e: any) => setFilters({ ...filters, lookback: e.target.value })}
              />
            </div>
            <div>
              <Label>Status</Label>
              <Select
                value={filters.status}
                onChange={(e: any) => setFilters({ ...filters, status: e.target.value })}
              >
                <SelectOption value="">any</SelectOption>
                <SelectOption value="ok">ok</SelectOption>
                <SelectOption value="error">error</SelectOption>
              </Select>
            </div>
            <div className="otel-filters-grow">
              <Label>Free text</Label>
              <Input
                value={filters.free}
                placeholder="model, tool, session…"
                onChange={(e: any) => setFilters({ ...filters, free: e.target.value })}
              />
            </div>
            <Button onClick={search} disabled={loading}>
              {loading ? "Searching…" : "Search"}
            </Button>
          </div>
        </CardContent>
      </Card>

      {error ? <ErrorBanner error={error} /> : null}

      <div className="otel-traces-split">
        <div className="otel-traces-list">
          {results == null ? (
            <div className="otel-loading">Loading…</div>
          ) : results.length === 0 ? (
            <EmptyState title="No traces" hint="Widen the lookback or run a turn." />
          ) : (
            results.map((t) => {
              const id = t.traceID || t.traceId;
              return (
                <div
                  key={id}
                  className={"otel-trace-card" + (selected === id ? " otel-trace-card-active" : "")}
                  onClick={() => setSelected(id)}
                >
                  <div className="otel-trace-card-top">
                    <span className="otel-trace-name">{t.rootTraceName || t.rootServiceName || id.slice(0, 12)}</span>
                    <span className="otel-trace-dur">{fmtDuration(t.durationMs)}</span>
                  </div>
                  <div className="otel-trace-card-sub">
                    {t.rootServiceName ? <span className="otel-chip">{t.rootServiceName}</span> : null}
                    {t.startTimeUnixNano ? (
                      <span className="otel-span-ago">{fmtAgo(Number(t.startTimeUnixNano))}</span>
                    ) : null}
                  </div>
                </div>
              );
            })
          )}
        </div>
        <div className="otel-traces-detail">
          {selected ? (
            <TraceDetail traceId={selected} />
          ) : (
            <EmptyState title="Select a trace" hint="Pick one on the left to see its span waterfall." />
          )}
        </div>
      </div>
    </div>
  );
}
