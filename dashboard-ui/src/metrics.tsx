// Metrics tab (#181, #284): curated panels plus an explorer over every
// instrument the source holds, all served by the bucket query endpoints (live
// store or a backend that serves metrics). Nothing is aggregated in the
// browser. The explorer runs on Query only; the panels refresh on a slow
// poll; range and explorer state live in the URL.
import { React, useState, useEffect, useCallback, useMemo, api, Input, Select, SelectOption, Button } from "./sdk";
import { fmtCost, fmtInt, fmtDurationMs, fmtAbsTime, fmtClock, metricOtlpName } from "./lib";
import { Stat, LineChart, MiniLabel, ErrorBanner, Empty, Series } from "./atoms";
import { usePolling } from "./poll";
import { useSource, withBackend } from "./source";
import { SourceSelect } from "./sourceselect";
import { readNav, writeNav } from "./nav";
import { RANGES, AGGS, explorerFromNav, navFromExplorer, ExplorerState } from "./params";
import { useActive } from "./index";

const POLL_MS = 30000;

export type Buckets = {
  name: string;
  agg: string;
  bucketS: number;
  buckets: number[];
  series: Record<string, (number | null)[]>;
  points: number;
  cumulative?: boolean;
  instrument?: string;
};

// Units for the instruments the plugin emits, by OTLP name. The live store
// records the same names as the backends since #95; a Prometheus-style name
// (hermes_token_usage, hermes_tool_duration_sum) is normalised in unit().
const UNITS: Record<string, string> = {
  "hermes.token.usage": "tokens",
  "hermes.cost.usage": "USD",
  "hermes.model.usage": "calls",
  "hermes.tool.duration": "ms",
  "hermes.approval.count": "approvals",
  "hermes.approval.duration": "ms",
  "hermes.message.count": "messages",
  "hermes.session.count": "sessions",
  "hermes.session.turns": "turns",
  "hermes.session.duration": "s",
  "hermes.prompt_cache.tokens": "tokens",
  "hermes.prompt_cache.observations": "observations",
  "hermes.api.error.count": "errors",
  "hermes.retry.count": "retries",
  "hermes.subagent.count": "runs",
  "hermes.subagent.duration": "ms",
  "hermes.skill.inferred": "hits",
  "gen_ai.client.token.usage": "tokens",
  "gen_ai.client.operation.duration": "s",
  "gen_ai.agent.token.usage": "tokens",
  "process.cpu.utilization": "ratio",
  "system.cpu.utilization": "ratio",
  "hw.gpu.utilization": "ratio",
  "hw.gpu.memory.usage": "bytes",
  "hw.power": "W",
};
const GROUP_KEYS = ["", "model", "provider", "token_type", "tool_name", "choice", "status", "operation", "error_type"];

export function seriesTotal(b: Buckets | null, label?: string): number {
  if (!b) return 0;
  const keys = label ? [label] : Object.keys(b.series);
  let t = 0;
  for (const k of keys) for (const v of b.series[k] || []) if (v != null) t += v;
  return t;
}

export function totalsByLabel(b: Buckets | null): { label: string; value: number }[] {
  if (!b) return [];
  return Object.keys(b.series)
    .map((label) => ({ label, value: seriesTotal(b, label) }))
    .sort((x, y) => y.value - x.value);
}

// Average of the per-bucket averages weighted by count is unavailable from
// buckets alone; for "avg" panels the server already averaged per bucket, so
// the panel shows the mean of buckets that have data.
export function meanByLabel(b: Buckets | null): { label: string; value: number }[] {
  if (!b) return [];
  return Object.keys(b.series)
    .map((label) => {
      const vals = (b.series[label] || []).filter((v): v is number => v != null);
      return { label, value: vals.length ? vals.reduce((a, v) => a + v, 0) / vals.length : 0 };
    })
    .sort((x, y) => y.value - x.value);
}

export function rangeLabel(r: { label: string; bucket: number }): string {
  const b = r.bucket >= 3600 ? `${r.bucket / 3600}h` : r.bucket >= 60 ? `${r.bucket / 60}m` : `${r.bucket}s`;
  return `last ${r.label} · ${b} buckets`;
}

/** The name to query for a canonical instrument, from the source's catalogue
 *  (contract §6). A histogram lands on a backend as ``_sum`` / ``_count`` (and
 *  ``_bucket``) streams; ``prefer`` picks the one the panel wants. */
export function resolveInstrument(names: { name: string; otlp_name?: string }[], otlp: string, prefer?: string): string | null {
  const matches = names.filter((n) => n.otlp_name === otlp || n.name === otlp || metricOtlpName(n.name) === otlp).map((n) => n.name);
  if (!matches.length) return null;
  if (prefer) {
    const hit = matches.find((n) => n.endsWith(prefer));
    if (hit) return hit;
  }
  // The bare name first (the live store, Uptrace, SigNoz), then any spelling.
  return matches.find((n) => n === otlp) || matches.find((n) => !/_(sum|count|bucket|total)$/.test(n)) || matches[0];
}

const PALETTE = [
  "var(--otel-chart-1)",
  "var(--otel-chart-2)",
  "var(--otel-chart-3)",
  "var(--otel-chart-4)",
  "var(--otel-chart-5)",
  "var(--otel-chart-6)",
  "var(--otel-chart-7)",
  "var(--otel-chart-8)",
];

function BarList({ rows, fmt, color }: { rows: { label: string; value: number }[]; fmt?: (n: number) => string; color?: string }) {
  if (!rows.length) return <div className="py-3 text-xs text-muted-foreground">No data in this range.</div>;
  const max = Math.max(1e-9, ...rows.map((r) => r.value));
  const top = rows.slice(0, 10);
  return (
    <div className="space-y-1.5">
      {top.map((r) => (
        <div
          key={r.label}
          className="flex items-center gap-2"
          title={`${r.label === "_" ? "all" : r.label}: ${fmt ? fmt(r.value) : fmtInt(Math.round(r.value))}`}
        >
          <span className="otel-w-28 shrink-0 truncate font-mono text-[11px] text-muted-foreground">{r.label === "_" ? "all" : r.label}</span>
          <div className="relative h-4 flex-1 bg-muted/30" role="img" aria-label={`${r.label}: ${fmt ? fmt(r.value) : fmtInt(Math.round(r.value))}`}>
            <div className="absolute inset-y-0 left-0" style={{ width: `${(r.value / max) * 100}%`, background: color || "var(--otel-chart-2)" }} />
          </div>
          <span className="otel-w-16 shrink-0 text-right tabular-nums text-xs">{fmt ? fmt(r.value) : fmtInt(Math.round(r.value))}</span>
        </div>
      ))}
      {rows.length > top.length ? <div className="text-[10px] text-muted-foreground">+{rows.length - top.length} more series</div> : null}
    </div>
  );
}

function Panel({ title, sub, children }: { title: string; sub?: string; children: any }) {
  return (
    <div className="otel-card-bg border border-border p-3">
      <div className="flex items-baseline justify-between gap-2">
        <MiniLabel>{title}</MiniLabel>
        {sub ? <span className="text-[10px] text-muted-foreground">{sub}</span> : null}
      </div>
      <div className="mt-2">{children}</div>
    </div>
  );
}

function Chart({ b, fmt, error }: { b: Buckets | null; fmt?: (n: number) => string; error?: unknown }) {
  if (error) return <ErrorBanner error={error} />;
  if (!b || !Object.keys(b.series).length) return <div className="py-3 text-xs text-muted-foreground">No data in this range.</div>;
  const all = Object.keys(b.series);
  const series: Series[] = all
    .slice(0, 8)
    .map((label, i) => ({ label: label === "_" ? b.name : label, color: PALETTE[i % PALETTE.length], points: b.series[label] }));
  const n = b.buckets.length;
  const withDate = b.bucketS >= 3600;
  const labels = [0, Math.floor(n / 2), n - 1].map((i) => (withDate ? fmtAbsTime(b.buckets[i]).slice(5, 16) : fmtClock(b.buckets[i])));
  const bucketLabels = b.buckets.map((t) => fmtAbsTime(t));
  return (
    <div>
      <LineChart series={series} labels={labels} fmt={fmt} bucketLabels={bucketLabels} />
      {all.length > 8 ? <div className="text-[10px] text-muted-foreground">showing 8 of {all.length} series</div> : null}
    </div>
  );
}

type PanelDef = { key: string; otlp: string; group: string; agg: { live: string; backend: string }; prefer?: string };
const PANELS: PanelDef[] = [
  { key: "tokens", otlp: "hermes.token.usage", group: "token_type", agg: { live: "sum", backend: "sum" } },
  { key: "cost", otlp: "hermes.cost.usage", group: "", agg: { live: "sum", backend: "sum" } },
  { key: "calls", otlp: "hermes.model.usage", group: "model", agg: { live: "count", backend: "sum" } },
  // the duration histogram: its _sum on a backend, the raw points on live
  { key: "tools", otlp: "hermes.tool.duration", group: "tool_name", agg: { live: "avg", backend: "sum" }, prefer: "_sum" },
  // the histogram's _count is the number of tool calls on a backend
  { key: "toolcalls", otlp: "hermes.tool.duration", group: "tool_name", agg: { live: "count", backend: "sum" }, prefer: "_count" },
  { key: "approvals", otlp: "hermes.approval.count", group: "choice", agg: { live: "count", backend: "sum" } },
  { key: "cache", otlp: "hermes.prompt_cache.tokens", group: "token_type", agg: { live: "sum", backend: "sum" } },
  { key: "cpu", otlp: "process.cpu.utilization", group: "", agg: { live: "avg", backend: "avg" } },
  { key: "gpu", otlp: "hw.gpu.utilization", group: "", agg: { live: "avg", backend: "avg" } },
];

export function MetricsPage() {
  const { source, setSource, status, isLive } = useSource();
  const active = useActive();
  const [ex, setEx] = useState<ExplorerState>(() => explorerFromNav(readNav()));
  const range = ex.range;
  const [names, setNames] = useState<{ name: string; otlp_name?: string; count?: number; instrument?: string }[]>([]);
  const [error, setError] = useState<unknown>(null);
  const [panels, setPanels] = useState<Record<string, Buckets | null>>({});
  const [panelErrors, setPanelErrors] = useState<Record<string, unknown>>({});
  const [loaded, setLoaded] = useState(false);
  // explorer draft (applied on Query)
  const [customGroup, setCustomGroup] = useState("");
  const [explore, setExplore] = useState<Buckets | null>(null);
  const [exploreError, setExploreError] = useState<unknown>(null);
  const [exploring, setExploring] = useState(false);

  const base = isLive ? "/live" : "";
  const entry = (status?.available || []).find((b: any) => b.name === source) || null;
  // The entry says what the type can do; the active adapter instance says what this entry does (lgtm vs tempo).
  const canQuery = isLive || !!(entry?.metrics || (status?.active === source && status?.metrics));

  useEffect(() => {
    if (active) writeNav(navFromExplorer(ex));
  }, [ex, active]);

  const query = useCallback(
    async (name: string, group: string, aggregate: string): Promise<Buckets> => {
      const p = withBackend(new URLSearchParams({ name, agg: aggregate, lookback_hours: String(range.hours), bucket_s: String(range.bucket) }), source);
      if (group) p.set("group_by", group);
      return api(`${base}/metrics/query`, p);
    },
    [base, range, source]
  );

  const load = useCallback(async () => {
    if (!canQuery) return;
    try {
      const p = withBackend(new URLSearchParams({ lookback_hours: String(range.hours) }), source);
      const r = await api(`${base}/metrics/names`, p);
      const list: { name: string; otlp_name?: string; count?: number }[] = r.names || [];
      setNames(list);
      setError(null);
      const out: Record<string, Buckets | null> = {};
      const errs: Record<string, unknown> = {};
      await Promise.all(
        PANELS.map(async (def) => {
          const native = resolveInstrument(list, def.otlp, isLive ? undefined : def.prefer);
          if (!native) {
            out[def.key] = null;
            return;
          }
          // A backend that keeps the histogram as one instrument (Uptrace,
          // SigNoz) has no _count series: count its observations instead.
          const aggregate = isLive ? def.agg.live : def.prefer === "_count" && !native.endsWith("_count") ? "count" : def.agg.backend;
          try {
            out[def.key] = await query(native, def.group, aggregate);
          } catch (e) {
            out[def.key] = null;
            errs[def.key] = e;
          }
        })
      );
      setPanels(out);
      setPanelErrors(errs);
    } catch (e: unknown) {
      setError(e);
    } finally {
      setLoaded(true);
    }
  }, [base, canQuery, isLive, query, range.hours, source]);

  useEffect(() => {
    setLoaded(false);
    load();
  }, [load]);
  usePolling(load, POLL_MS, active && canQuery);

  const runExplore = useCallback(async () => {
    if (!ex.instrument) return;
    setExploring(true);
    setExploreError(null);
    try {
      setExplore(await query(ex.instrument, ex.groupBy, ex.agg));
    } catch (e) {
      setExploreError(e);
      setExplore(null);
    } finally {
      setExploring(false);
    }
  }, [ex.agg, ex.groupBy, ex.instrument, query]);
  // A pasted link with an instrument runs once; afterwards only the button does.
  const ranOnce = useState({ done: false })[0];
  useEffect(() => {
    if (ex.instrument && !ranOnce.done && names.length) {
      ranOnce.done = true;
      runExplore();
    }
  }, [ex.instrument, names.length, runExplore, ranOnce]);

  const tokens = panels.tokens || null;
  const cost = panels.cost || null;
  const calls = panels.calls || null;
  const tools = panels.tools || null;
  const toolCallsB = panels.toolcalls || null;
  const toolCalls = isLive ? (tools ? fmtInt(tools.points) : null) : toolCallsB ? fmtInt(Math.round(seriesTotal(toolCallsB))) : null;
  const approvals = panels.approvals || null;
  const cache = panels.cache || null;
  const totalTokens = tokens ? seriesTotal(tokens) : null;
  const totalCost = cost && cost.points ? seriesTotal(cost) : null;
  // Cache-read share: the hermes.token.usage series carries a cacheRead token
  // type next to input; hermes.prompt_cache.tokens (when recorded) is the same fact.
  const tokenRows = useMemo(() => totalsByLabel(tokens), [tokens]);
  const cacheRows = useMemo(() => totalsByLabel(cache), [cache]);
  const cacheRead = tokenRows.find((r) => /cache/i.test(r.label))?.value ?? cacheRows.find((r) => /read|hit/i.test(r.label))?.value ?? null;
  const cacheAll = tokenRows.find((r) => r.label === "input")?.value ?? cacheRows.reduce((a, r) => a + r.value, 0);
  const unit = (n: string) => UNITS[n] || UNITS[metricOtlpName(n)] || "";
  const availableSources = (status?.available || []).filter((b: any) => b.metrics).map((b: any) => b.name);

  const header = (
    <div className="flex flex-wrap items-center justify-between gap-2">
      <div className="flex flex-wrap items-center gap-3">
        <SourceSelect source={source} onChange={setSource} status={status} need="metrics" />
        <Select
          value={String(range.hours)}
          onValueChange={(v: string) => setEx((s) => ({ ...s, range: RANGES.find((r) => String(r.hours) === v) || RANGES[1] }))}
          className="otel-w-56 h-8"
          aria-label="range"
        >
          {RANGES.map((r) => (
            <SelectOption key={r.label} value={String(r.hours)}>
              {rangeLabel(r)}
            </SelectOption>
          ))}
        </Select>
      </div>
      <span className="text-xs text-muted-foreground">
        {names.length} instrument{names.length === 1 ? "" : "s"}
        {isLive ? " in the store" : " in this range"}
      </span>
    </div>
  );

  if (!canQuery)
    return (
      <div className="space-y-3">
        {header}
        <Empty title="This source does not serve metrics">
          Pick the Live source{availableSources.length ? ` or one of: ${availableSources.join(", ")}` : ", or configure a backend whose adapter serves metrics"}
          .
        </Empty>
      </div>
    );

  return (
    <div className="space-y-3">
      {header}
      {error ? <ErrorBanner error={error} prefix="Metrics" /> : null}
      {loaded && names.length === 0 && !error ? (
        <Empty title="No metrics in this range">Run a Hermes turn, or widen the range. Token usage, cost, tool durations and approvals appear here.</Empty>
      ) : (
        <>
          <div className="otel-kpi-grid">
            <Stat label="Tokens" value={totalTokens != null ? fmtInt(Math.round(totalTokens)) : null} unknownText="not recorded" />
            <Stat label="Cost" value={totalCost != null ? fmtCost(totalCost) : null} unknownText="no pricing data" accent="cost" />
            <Stat label="Model calls" value={calls ? fmtInt(Math.round(seriesTotal(calls))) : null} unknownText="not recorded" />
            <Stat
              label="Tool calls"
              value={toolCalls}
              unknownText="not recorded"
              sub={isLive ? "duration points, one per call" : "from the duration histogram's count"}
            />
            <Stat
              label="Cache read"
              value={cacheRead != null && cacheAll ? `${Math.round((cacheRead / cacheAll) * 100)}%` : null}
              sub={cacheRead != null ? `${fmtInt(Math.round(cacheRead))} of ${fmtInt(Math.round(cacheAll))} input tokens` : undefined}
              unknownText="no cache data"
            />
          </div>

          <div className="grid gap-3 lg:grid-cols-2">
            <Panel title="Tokens over time" sub={`by token_type · per ${range.bucket}s`}>
              <Chart b={tokens} error={panelErrors.tokens} />
            </Panel>
            <Panel title="Cost over time" sub={cost && cost.points ? `USD · per ${range.bucket}s` : "no pricing data for the models used"}>
              <Chart b={cost} fmt={fmtCost} error={panelErrors.cost} />
            </Panel>
            <Panel title="Tokens by type">
              <BarList rows={totalsByLabel(tokens)} color="var(--otel-chart-1)" />
            </Panel>
            <Panel title="Calls by model">
              <BarList rows={totalsByLabel(calls)} color="var(--otel-chart-4)" />
            </Panel>
            <Panel title={isLive ? "Avg tool duration" : "Tool duration (sum)"} sub="ms">
              <BarList rows={isLive ? meanByLabel(tools) : totalsByLabel(tools)} fmt={fmtDurationMs} color="var(--otel-chart-3)" />
            </Panel>
            <Panel title="Approvals by choice">
              <BarList rows={totalsByLabel(approvals)} color="var(--otel-chart-5)" />
            </Panel>
            {panels.cpu ? (
              <Panel title="CPU" sub="utilisation ratio, avg per bucket">
                <Chart b={panels.cpu} error={panelErrors.cpu} />
              </Panel>
            ) : null}
            {panels.gpu ? (
              <Panel title="GPU" sub="utilisation ratio, avg per bucket">
                <Chart b={panels.gpu} error={panelErrors.gpu} />
              </Panel>
            ) : null}
          </div>

          <Panel title="Explore any instrument" sub="server-side buckets; group by an attribute">
            <form
              className="otel-search-grid"
              onSubmit={(e: any) => {
                e.preventDefault();
                setEx((s) => ({ ...s, groupBy: customGroup.trim() || s.groupBy }));
                runExplore();
              }}
            >
              <Select value={ex.instrument} onValueChange={(v: string) => setEx((s) => ({ ...s, instrument: v }))} className="h-8" aria-label="instrument">
                <SelectOption value="">pick an instrument…</SelectOption>
                {names.map((n) => (
                  <SelectOption key={n.name} value={n.name}>
                    {`${n.name}${n.count != null ? ` (${n.count})` : ""}${unit(n.otlp_name || n.name) ? ` · ${unit(n.otlp_name || n.name)}` : ""}`}
                  </SelectOption>
                ))}
              </Select>
              <Select
                value={GROUP_KEYS.includes(ex.groupBy) ? ex.groupBy : ""}
                onValueChange={(v: string) => setEx((s) => ({ ...s, groupBy: v }))}
                className="h-8"
                aria-label="group by"
              >
                {GROUP_KEYS.map((k) => (
                  <SelectOption key={k} value={k}>
                    {k ? `group by ${k}` : "no grouping"}
                  </SelectOption>
                ))}
              </Select>
              <Input
                className="h-8"
                placeholder="or any attribute"
                value={customGroup}
                onChange={(e: any) => setCustomGroup(e.target.value)}
                aria-label="custom group by"
              />
              <Select value={ex.agg} onValueChange={(v: string) => setEx((s) => ({ ...s, agg: v }))} className="h-8" aria-label="aggregation">
                {AGGS.map((a) => (
                  <SelectOption key={a} value={a}>
                    {a}
                  </SelectOption>
                ))}
              </Select>
              <Button type="submit" size="sm" disabled={!ex.instrument || exploring}>
                {exploring ? "Querying…" : "Query"}
              </Button>
            </form>
            {ex.instrument ? (
              <div className="mt-3 space-y-3">
                <Chart b={explore} error={exploreError} />
                <div className="text-[11px] text-muted-foreground">
                  {explore
                    ? `${explore.points} point${explore.points === 1 ? "" : "s"} · ${Object.keys(explore.series).length} series · ${explore.agg} per ${explore.bucketS}s${unit(ex.instrument) ? ` · ${unit(ex.instrument)}` : ""}${explore.cumulative ? " · cumulative counter shown as increases" : ""}${explore.instrument ? ` · ${explore.instrument}` : ""}`
                    : exploreError
                      ? ""
                      : "press Query"}
                </div>
                <BarList rows={ex.agg === "avg" ? meanByLabel(explore) : totalsByLabel(explore)} />
              </div>
            ) : null}
          </Panel>
        </>
      )}
    </div>
  );
}
