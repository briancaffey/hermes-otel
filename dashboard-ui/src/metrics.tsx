// Metrics tab (#181): an explorer over every instrument the source holds,
// plus curated panels, all served by the bucket query endpoints (live store
// or a backend that serves metrics). Nothing is aggregated in the browser.
import { React, useState, useEffect, useCallback, useMemo, fetchJSON, API, Input, Select, SelectOption, Button } from "./sdk";
import { fmtCost, fmtInt, fmtDurationMs, fmtAbsTime, metricOtlpName } from "./lib";
import { Stat, LineChart, MiniLabel, ErrorBanner } from "./atoms";
import { usePolling } from "./poll";
import { useSource, withBackend } from "./source";
import { SourceSelect } from "./sourceselect";

/* eslint-disable @typescript-eslint/no-explicit-any */
const POLL_MS = 15000;

type Buckets = { name: string; agg: string; bucketS: number; buckets: number[]; series: Record<string, (number | null)[]>; points: number; cumulative?: boolean };

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
const RANGES: { label: string; hours: number; bucket: number }[] = [
  { label: "15m", hours: 0.25, bucket: 15 },
  { label: "1h", hours: 1, bucket: 60 },
  { label: "6h", hours: 6, bucket: 300 },
  { label: "24h", hours: 24, bucket: 900 },
  { label: "7d", hours: 168, bucket: 3600 * 3 },
];

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

const PALETTE = ["#38bdf8", "#34d399", "#fbbf24", "#a78bfa", "#f472b6", "#22d3ee", "#6ee7b7", "#94a3b8"];

function BarList({ rows, fmt, color }: { rows: { label: string; value: number }[]; fmt?: (n: number) => string; color?: string }) {
  if (!rows.length) return <div className="py-3 text-xs text-muted-foreground">No data in this range.</div>;
  const max = Math.max(1e-9, ...rows.map((r) => r.value));
  return (
    <div className="space-y-1.5">
      {rows.slice(0, 10).map((r) => (
        <div key={r.label} className="flex items-center gap-2">
          <span className="otel-w-28 shrink-0 truncate font-mono text-[11px] text-muted-foreground" title={r.label}>
            {r.label === "_" ? "all" : r.label}
          </span>
          <div className="relative h-4 flex-1 bg-muted/30">
            <div className="absolute inset-y-0 left-0" style={{ width: `${(r.value / max) * 100}%`, background: color || "var(--color-primary, #34d399)" }} />
          </div>
          <span className="otel-w-16 shrink-0 text-right tabular-nums text-xs">{fmt ? fmt(r.value) : fmtInt(Math.round(r.value))}</span>
        </div>
      ))}
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

function Chart({ b, fmt }: { b: Buckets | null; fmt?: (n: number) => string }) {
  if (!b || !Object.keys(b.series).length) return <div className="py-3 text-xs text-muted-foreground">No data in this range.</div>;
  const series = Object.keys(b.series).slice(0, 8).map((label, i) => ({ label: label === "_" ? b.name : label, color: PALETTE[i % PALETTE.length], points: b.series[label].map((v) => v ?? 0) }));
  const n = b.buckets.length;
  const labels = [0, Math.floor(n / 2), n - 1].map((i) => fmtAbsTime(b.buckets[i]).replace(/^.*?, /, ""));
  return <LineChart series={series} labels={labels} fmt={fmt} />;
}

export function MetricsPage() {
  const { source, setSource, status, isLive } = useSource();
  const [range, setRange] = useState(RANGES[1]);
  const [names, setNames] = useState<{ name: string; count?: number }[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [panels, setPanels] = useState<Record<string, Buckets | null>>({});
  // explorer
  const [pick, setPick] = useState("");
  const [groupBy, setGroupBy] = useState("");
  const [customGroup, setCustomGroup] = useState("");
  const [agg, setAgg] = useState("sum");
  const [explore, setExplore] = useState<Buckets | null>(null);

  const base = isLive ? `${API}/live` : API;
  const canQuery = isLive || !!status?.metrics;

  const query = useCallback(
    async (name: string, group: string, aggregate: string): Promise<Buckets | null> => {
      const p = withBackend(new URLSearchParams({ name, agg: aggregate, lookback_hours: String(range.hours), bucket_s: String(range.bucket) }), source);
      if (group) p.set("group_by", group);
      try {
        return await fetchJSON(`${base}/metrics/query?${p}`);
      } catch {
        return null;
      }
    },
    [base, range, source]
  );

  const load = useCallback(async () => {
    if (!canQuery) return;
    try {
      const p = withBackend(new URLSearchParams({ lookback_hours: String(range.hours) }), source);
      const r = await fetchJSON(`${base}/metrics/names?${p}`);
      const list: { name: string; count?: number }[] = r.names || [];
      setNames(list);
      setError(null);
      const have = new Set(list.map((n) => n.name));
      const want: [string, string, string, string][] = [
        ["tokens", isLive ? "hermes.token.usage" : "hermes_token_usage", "token_type", "sum"],
        ["cost", isLive ? "hermes.cost.usage" : "hermes_cost_usage", "", "sum"],
        ["calls", isLive ? "hermes.model.usage" : "hermes_model_usage", "model", isLive ? "count" : "sum"],
        ["tools", isLive ? "hermes.tool.duration" : "hermes_tool_duration_sum", "tool_name", isLive ? "avg" : "sum"],
        ["approvals", isLive ? "hermes.approval.count" : "hermes_approval_count", "choice", isLive ? "count" : "sum"],
        ["cache", isLive ? "hermes.prompt_cache.tokens" : "hermes_prompt_cache_tokens", "token_type", "sum"],
        ["cpu", "process.cpu.utilization", "", "avg"],
        ["gpu", "hw.gpu.utilization", "", "avg"],
      ];
      const out: Record<string, Buckets | null> = {};
      await Promise.all(
        want.map(async ([key, name, group, aggregate]) => {
          out[key] = have.has(name) ? await query(name, group, aggregate) : null;
        })
      );
      setPanels(out);
    } catch (e: any) {
      setError(String(e?.message || e));
    }
  }, [base, canQuery, isLive, query, range.hours, source]);

  useEffect(() => {
    load();
  }, [load]);
  usePolling(load, POLL_MS, canQuery);

  const runExplore = useCallback(async () => {
    if (!pick) return;
    setExplore(await query(pick, customGroup.trim() || groupBy, agg));
  }, [agg, customGroup, groupBy, pick, query]);
  useEffect(() => {
    runExplore();
  }, [runExplore]);

  const tokens = panels.tokens || null;
  const cost = panels.cost || null;
  const calls = panels.calls || null;
  const tools = panels.tools || null;
  const approvals = panels.approvals || null;
  const cache = panels.cache || null;
  const totalTokens = seriesTotal(tokens);
  const totalCost = seriesTotal(cost);
  // Cache-read share: the hermes.token.usage series carries a cacheRead token
  // type next to input; hermes.prompt_cache.tokens (when recorded) is the same fact.
  const tokenRows = useMemo(() => totalsByLabel(tokens), [tokens]);
  const cacheRows = useMemo(() => totalsByLabel(cache), [cache]);
  const cacheRead = tokenRows.find((r) => /cache/i.test(r.label))?.value ?? cacheRows.find((r) => /read|hit/i.test(r.label))?.value ?? null;
  const cacheAll = tokenRows.find((r) => r.label === "input")?.value ?? cacheRows.reduce((a, r) => a + r.value, 0);
  const unit = (n: string) => UNITS[n] || UNITS[metricOtlpName(n)] || "";

  const header = (
    <div className="flex flex-wrap items-center justify-between gap-2">
      <div className="flex flex-wrap items-center gap-3">
        <SourceSelect source={source} onChange={setSource} status={status} need="metrics" />
        <Select value={String(range.hours)} onValueChange={(v: string) => setRange(RANGES.find((r) => String(r.hours) === v) || RANGES[1])} className="otel-w-56 h-8">
          {RANGES.map((r) => (
            <SelectOption key={r.label} value={String(r.hours)}>
              {rangeLabel(r)}
            </SelectOption>
          ))}
        </Select>
      </div>
      <span className="text-xs text-muted-foreground">{names.length} instrument{names.length === 1 ? "" : "s"} in this range</span>
    </div>
  );

  if (!canQuery)
    return (
      <div className="space-y-3">
        {header}
        <div className="border border-dashed border-border px-4 py-12 text-center text-sm text-muted-foreground">
          <div className="mb-1 text-base font-medium text-foreground">This source does not serve metrics</div>
          Pick the Live source, or a backend whose adapter serves metrics (OpenObserve).
        </div>
      </div>
    );

  return (
    <div className="space-y-3">
      {header}
      {error ? <ErrorBanner error={error} /> : null}
      {names.length === 0 ? (
        <div className="border border-dashed border-border px-4 py-12 text-center text-sm text-muted-foreground">
          <div className="mb-1 text-base font-medium text-foreground">No metrics in this range</div>
          Run a Hermes turn, or widen the range. Token usage, cost, tool durations and approvals appear here.
        </div>
      ) : (
        <>
          <div className="otel-kpi-grid">
            <Stat label="Tokens" value={fmtInt(Math.round(totalTokens))} />
            <Stat label="Cost" value={cost && cost.points ? fmtCost(totalCost) : "—"} sub={cost && cost.points ? undefined : "no pricing data"} accent={cost && cost.points ? "cost" : undefined} />
            <Stat label="Model calls" value={fmtInt(Math.round(seriesTotal(calls)))} />
            <Stat label="Tool calls" value={tools ? fmtInt(tools.points) : "0"} />
            <Stat label="Cache read" value={cacheRead != null && cacheAll ? `${Math.round((cacheRead / cacheAll) * 100)}%` : "—"} sub={cacheRead != null ? `${fmtInt(Math.round(cacheRead))} of ${fmtInt(Math.round(cacheAll))} input tokens` : "no cache data"} />
          </div>

          <div className="grid gap-3 lg:grid-cols-2">
            <Panel title="Tokens over time" sub={`by token_type · per ${range.bucket}s`}>
              <Chart b={tokens} />
            </Panel>
            <Panel title="Cost over time" sub={cost && cost.points ? `USD · per ${range.bucket}s` : "no pricing data for the models used"}>
              <Chart b={cost} fmt={fmtCost} />
            </Panel>
            <Panel title="Tokens by type"><BarList rows={totalsByLabel(tokens)} color="#38bdf8" /></Panel>
            <Panel title="Calls by model"><BarList rows={totalsByLabel(calls)} color="#a78bfa" /></Panel>
            <Panel title={isLive ? "Avg tool duration" : "Tool duration (sum)"} sub="ms">
              <BarList rows={isLive ? meanByLabel(tools) : totalsByLabel(tools)} fmt={fmtDurationMs} color="#fbbf24" />
            </Panel>
            <Panel title="Approvals by choice"><BarList rows={totalsByLabel(approvals)} color="#f472b6" /></Panel>
            {panels.cpu || panels.gpu ? (
              <Panel title="Host" sub="utilisation ratio, avg per bucket">
                <Chart b={panels.cpu || panels.gpu} />
              </Panel>
            ) : null}
          </div>

          <Panel title="Explore any instrument" sub="server-side buckets; group by an attribute">
            <div className="otel-search-grid">
              <Select value={pick} onValueChange={setPick} className="h-8">
                <SelectOption value="">pick an instrument…</SelectOption>
                {names.map((n) => (
                  <SelectOption key={n.name} value={n.name}>
                    {n.name}
                    {n.count != null ? ` (${n.count})` : ""}
                    {unit(n.name) ? ` · ${unit(n.name)}` : ""}
                  </SelectOption>
                ))}
              </Select>
              <Select value={groupBy} onValueChange={setGroupBy} className="h-8">
                {GROUP_KEYS.map((k) => (
                  <SelectOption key={k} value={k}>
                    {k ? `group by ${k}` : "no grouping"}
                  </SelectOption>
                ))}
              </Select>
              <Input className="h-8" placeholder="or any attribute" value={customGroup} onChange={(e: any) => setCustomGroup(e.target.value)} />
              <Select value={agg} onValueChange={setAgg} className="h-8">
                {["sum", "count", "avg", "max", "last"].map((a) => (
                  <SelectOption key={a} value={a}>
                    {a}
                  </SelectOption>
                ))}
              </Select>
              <Button type="button" size="sm" onClick={runExplore} disabled={!pick}>
                Query
              </Button>
            </div>
            {pick ? (
              <div className="mt-3 space-y-3">
                <Chart b={explore} />
                <div className="text-[11px] text-muted-foreground">
                  {explore ? `${explore.points} point${explore.points === 1 ? "" : "s"} · ${Object.keys(explore.series).length} series · ${explore.agg} per ${explore.bucketS}s${unit(pick) ? ` · ${unit(pick)}` : ""}${explore.cumulative ? " · cumulative counter shown as increases" : ""}` : "no data"}
                </div>
                <BarList rows={agg === "avg" ? meanByLabel(explore) : totalsByLabel(explore)} />
              </div>
            ) : null}
          </Panel>
        </>
      )}
    </div>
  );
}
