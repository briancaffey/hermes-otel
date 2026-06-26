import { React, useState, useEffect, useRef, useCallback, fetchJSON, API } from "./sdk";
import { fmtCost, fmtInt, fmtDurationMs } from "./lib";
import { Stat, LineChart, MiniLabel } from "./atoms";

/* eslint-disable @typescript-eslint/no-explicit-any */
type Metric = { name: string; value: number; attributes: Record<string, any>; time_unix_nano: number; seq: number };
const POLL_MS = 2000;

// Horizontal bar list (e.g. tokens by type, calls by model).
function BarList({ rows, fmt, color }: { rows: { label: string; value: number }[]; fmt?: (n: number) => string; color?: string }) {
  if (!rows.length) return <div className="py-3 text-xs text-muted-foreground">No data yet.</div>;
  const max = Math.max(1, ...rows.map((r) => r.value));
  return (
    <div className="space-y-1.5">
      {rows.map((r) => (
        <div key={r.label} className="flex items-center gap-2">
          <span className="w-28 shrink-0 truncate font-mono text-[11px] text-muted-foreground" title={r.label}>
            {r.label}
          </span>
          <div className="relative h-4 flex-1 bg-muted/30">
            <div className="absolute inset-y-0 left-0" style={{ width: `${(r.value / max) * 100}%`, background: color || "var(--color-primary, #34d399)" }} />
          </div>
          <span className="w-16 shrink-0 text-right tabular-nums text-xs">{fmt ? fmt(r.value) : fmtInt(r.value)}</span>
        </div>
      ))}
    </div>
  );
}

function Panel({ title, children }: { title: string; children: any }) {
  return (
    <div className="border border-border bg-card/40 p-3">
      <MiniLabel>{title}</MiniLabel>
      <div className="mt-2">{children}</div>
    </div>
  );
}

export function MetricsPage() {
  const [metrics, setMetrics] = useState<Metric[]>([]);
  const [live, setLive] = useState<boolean | null>(null);
  const cursor = useRef(0);

  const poll = useCallback(async () => {
    try {
      const st = await fetchJSON(`${API}/live/status`);
      setLive(st && st.live !== false);
      if (!st || st.live === false) return;
      const mt = await fetchJSON(`${API}/live/metrics?since=${cursor.current}&limit=5000`);
      cursor.current = Math.max(mt.cursor || 0, cursor.current);
      if (mt.metrics?.length) setMetrics((prev) => [...prev, ...mt.metrics].slice(-8000));
    } catch {
      /* keep last */
    }
  }, []);
  useEffect(() => {
    poll();
  }, [poll]);
  useEffect(() => {
    const id = setInterval(poll, POLL_MS);
    return () => clearInterval(id);
  }, [poll]);

  // ── aggregate ──────────────────────────────────────────────────────────
  const sumBy = (name: string, attr?: string) => {
    const out: Record<string, number> = {};
    for (const m of metrics) {
      if (m.name !== name) continue;
      const key = attr ? String(m.attributes[attr] ?? "—") : "_";
      out[key] = (out[key] || 0) + m.value;
    }
    return out;
  };
  const countBy = (name: string, attr: string) => {
    const out: Record<string, number> = {};
    for (const m of metrics) {
      if (m.name !== name) continue;
      const key = String(m.attributes[attr] ?? "—");
      out[key] = (out[key] || 0) + 1;
    }
    return out;
  };
  const total = (name: string) => metrics.filter((m) => m.name === name).reduce((a, m) => a + m.value, 0);

  const tokensByType = sumBy("token_usage", "token_type");
  const totalTokens = Object.values(tokensByType).reduce((a, b) => a + b, 0);
  const totalCost = total("cost_usage");
  const modelCalls = countBy("model_usage", "model");
  const approvalsByChoice = countBy("approval_count", "choice");
  const toolDur = (() => {
    const sum: Record<string, number> = {};
    const cnt: Record<string, number> = {};
    for (const m of metrics) {
      if (m.name !== "tool_duration") continue;
      const k = String(m.attributes["tool_name"] ?? "—");
      sum[k] = (sum[k] || 0) + m.value;
      cnt[k] = (cnt[k] || 0) + 1;
    }
    return Object.keys(sum).map((k) => ({ label: k, value: sum[k] / cnt[k] }));
  })();

  // time series — cost + tokens bucketed over the last ~5 min (20 × 15s)
  const now = Date.now();
  const BUCKETS = 24;
  const SPAN = 15000;
  const costSeries = new Array(BUCKETS).fill(0);
  const tokenSeries = new Array(BUCKETS).fill(0);
  for (const m of metrics) {
    const t = m.time_unix_nano / 1e6;
    const idx = BUCKETS - 1 - Math.floor((now - t) / SPAN);
    if (idx < 0 || idx >= BUCKETS) continue;
    if (m.name === "cost_usage") costSeries[idx] += m.value;
    if (m.name === "token_usage") tokenSeries[idx] += m.value;
  }

  const toRows = (o: Record<string, number>) =>
    Object.entries(o)
      .map(([label, value]) => ({ label, value }))
      .sort((a, b) => b.value - a.value)
      .slice(0, 8);

  if (live === false)
    return (
      <div className="border border-dashed border-border px-4 py-12 text-center text-sm text-muted-foreground">
        <div className="mb-1 text-base font-medium text-foreground">Metrics need the live store</div>
        Metrics are read from the in-process store (dashboard_live). Run a turn to populate them.
      </div>
    );

  if (!metrics.length)
    return (
      <div className="border border-dashed border-border px-4 py-12 text-center text-sm text-muted-foreground">
        <div className="mb-1 text-base font-medium text-foreground">No metrics yet</div>
        Run a Hermes turn — token usage, cost, tool durations, and approvals appear here live.
      </div>
    );

  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-5">
        <Stat label="Total tokens" value={fmtInt(totalTokens)} />
        <Stat label="Total cost" value={fmtCost(totalCost)} accent="cost" />
        <Stat label="Model calls" value={fmtInt(metrics.filter((m) => m.name === "model_usage").length)} />
        <Stat label="Tool calls" value={fmtInt(metrics.filter((m) => m.name === "tool_duration").length)} />
        <Stat label="Approvals" value={fmtInt(metrics.filter((m) => m.name === "approval_count").length)} />
      </div>

      <div className="grid gap-3 lg:grid-cols-2">
        <Panel title="Tokens / 15s">
          <LineChart series={[{ label: "tokens", color: "#38bdf8", points: tokenSeries }]} />
        </Panel>
        <Panel title="Cost / 15s">
          <LineChart series={[{ label: "$ cost", color: "#34d399", points: costSeries }]} />
        </Panel>
        <Panel title="Tokens by type">
          <BarList rows={toRows(tokensByType)} color="#38bdf8" />
        </Panel>
        <Panel title="Calls by model">
          <BarList rows={toRows(modelCalls)} color="#a78bfa" />
        </Panel>
        <Panel title="Avg tool duration">
          <BarList rows={toolDur.sort((a, b) => b.value - a.value).slice(0, 8)} fmt={fmtDurationMs} color="#fbbf24" />
        </Panel>
        <Panel title="Approvals by choice">
          <BarList rows={toRows(approvalsByChoice)} color="#f472b6" />
        </Panel>
      </div>
    </div>
  );
}
