// Live tab (#280): the last hour of turns from the live store's own query
// (/live/traces, the same rows and totals the Traces tab shows), KPIs over
// that window, an activity sparkline built from the rows, and one card per
// turn. Nothing is assembled from raw spans in the browser any more, so a
// turn never renders from a partial span set and the first load is one page.
import { React, useState, useEffect, useRef, useCallback, api, Button, Checkbox } from "./sdk";
import { LiveSpan, LiveTrace, TraceRow, rowFromLive, Kind, KIND_COLOR, liveTreeFromSpans, isMcpKeepalivePing, fmtCost, fmtInt, findRoot } from "./lib";
import { Stat, Sparkline, Pulse, MiniLabel, ErrorBanner, Empty, Toggle } from "./atoms";
import { IconPause, IconPlay } from "./icons";
import { TraceCard, LiveTraceDetail } from "./spantree";
import { usePolling } from "./poll";
import { readNav, writeNav } from "./nav";
import { useActive } from "./index";

const POLL_MS = 2000;
const WINDOW_H = 1;
const PAGE = 50;
const BUCKETS = 50;
const BUCKET_S = 2;

export function deriveStats(rows: TraceRow[]) {
  let cost: number | null = null;
  let tokens: number | null = null;
  let errors = 0;
  let spans = 0;
  let spansKnown = true;
  const byKind: Partial<Record<Kind, number>> = {};
  for (const t of rows) {
    if (t.cost != null) cost = (cost || 0) + t.cost;
    if (t.tokens != null) tokens = (tokens || 0) + t.tokens;
    if (t.error) errors++;
    if (t.spanCount == null) spansKnown = false;
    else spans += t.spanCount;
    byKind[t.rootKind] = (byKind[t.rootKind] || 0) + 1;
  }
  return { cost, tokens, errors, turns: rows.length, spans: spansKnown ? spans : null, byKind };
}

/** Spans per 2 s over the last 100 s, from the rows' end times and span counts. */
export function activityBuckets(rows: TraceRow[], now = Date.now()): number[] {
  const buckets = new Array(BUCKETS).fill(0);
  for (const t of rows) {
    const endMs = (t.endNs || t.startNs) / 1e6;
    const idx = BUCKETS - 1 - Math.floor((now - endMs) / (BUCKET_S * 1000));
    if (idx >= 0 && idx < BUCKETS) buckets[idx] += t.spanCount || 1;
  }
  return buckets;
}

export function LivePage() {
  const active = useActive();
  const [rows, setRows] = useState<TraceRow[]>([]);
  const [status, setStatus] = useState<any>(null);
  const [error, setError] = useState<unknown>(null);
  const [paused, setPaused] = useState(false);
  const [selected, setSelected] = useState<LiveTrace | null>(null);
  const [detailSpans, setDetailSpans] = useState<LiveSpan[] | null>(null);
  const [showPings, setShowPings] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const inflight = useRef(false);

  const poll = useCallback(async () => {
    if (inflight.current) return;
    inflight.current = true;
    try {
      const st = await api("/live/status");
      setStatus(st);
      if (!st || st.live === false) return;
      const r = await api("/live/traces", { lookback_hours: WINDOW_H, limit: PAGE });
      setRows((r.traces || []).map(rowFromLive));
      setError(null);
    } catch (e: unknown) {
      setError(e);
    } finally {
      inflight.current = false;
      setLoaded(true);
    }
  }, []);
  useEffect(() => {
    poll();
  }, [poll]);
  usePolling(poll, POLL_MS, active && !paused && !selected);

  // Detail: the trace's spans, refreshed while the turn is still running.
  const loadDetail = useCallback(async (id: string) => api(`/live/traces/${encodeURIComponent(id)}`), []);
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
  const refreshDetail = useCallback(() => {
    if (!selected) return;
    loadDetail(selected.traceId)
      .then((r: any) => setDetailSpans(r.spans || []))
      .catch(() => undefined);
  }, [selected?.traceId, loadDetail]);
  usePolling(refreshDetail, POLL_MS, active && !!selected && (!!selected.partial || (detailSpans != null && !findRoot(detailSpans))));

  // ?tab=live&trace=<id> opens the card after a refresh (#280).
  useEffect(() => {
    const t = readNav().trace;
    if (!t || readNav().tab !== "live") return;
    loadDetail(t)
      .then((r: any) => {
        if (r.trace) setSelected({ ...r.trace, spans: r.spans });
      })
      .catch(() => undefined);
  }, [loadDetail]);
  useEffect(() => {
    if (!active) return;
    writeNav({ trace: selected ? String(selected.traceId) : "" });
  }, [selected, active]);

  const allRows = rows;
  const shown = showPings ? allRows : allRows.filter((t) => !isMcpKeepalivePing(t.rootName, t.error));
  const hiddenPings = allRows.length - shown.length;
  const stats = deriveStats(shown);
  const buckets = activityBuckets(shown);
  const lastSession = shown.length ? shown[0].session : null;

  if (status && status.live === false) {
    return (
      <Empty title="Live store unavailable">
        {status.reason || "Set dashboard_live: true in the plugin config (it's on by default), then run a turn."}
        {status.path ? <div className="mt-1 font-mono text-xs">{status.path}</div> : null}
      </Empty>
    );
  }

  if (selected) {
    const spans = detailSpans || [];
    const { roots } = liveTreeFromSpans(spans);
    return (
      <div className="space-y-3">
        <LiveTraceDetail trace={{ ...selected, spans }} roots={roots} loading={detailSpans === null} onBack={() => setSelected(null)} />
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2.5">
          <Pulse active={!paused && (status?.spans || 0) > 0} />
          <span className="text-base font-semibold tracking-tight">Live agent activity</span>
          <span className="text-xs text-muted-foreground">
            {fmtInt(status?.spans)} spans buffered
            {lastSession ? (
              <>
                {" "}
                · session <span className="font-mono">{String(lastSession).slice(0, 12)}</span>
              </>
            ) : null}
          </span>
        </div>
        <Button variant="outline" size="sm" onClick={() => setPaused((p) => !p)} title={paused ? "resume following" : "stop following"}>
          {paused ? <IconPlay size={12} /> : <IconPause size={12} />}
          <span className="ml-1">{paused ? "Resume" : "Pause"}</span>
        </Button>
      </div>

      {error ? <ErrorBanner error={error} /> : null}
      {status?.write_error ? <ErrorBanner error={`0: ${status.write_error}`} prefix="Live store write failed" /> : null}

      <div className="flex items-center gap-2">
        <MiniLabel>
          last {WINDOW_H}h · up to {PAGE} newest turns
        </MiniLabel>
      </div>
      <div className="otel-kpi-grid">
        <Stat label="Cost" value={stats.cost != null ? fmtCost(stats.cost) : null} accent="cost" unknownText="no pricing data" />
        <Stat label="Tokens" value={stats.tokens != null ? fmtInt(stats.tokens) : null} unknownText="not recorded" />
        <Stat label="Turns" value={fmtInt(stats.turns)} />
        <Stat label="Spans" value={stats.spans != null ? fmtInt(stats.spans) : null} unknownText="unknown" />
        <Stat label="Errors" value={fmtInt(stats.errors)} accent={stats.errors ? "error" : undefined} />
      </div>

      <div className="otel-card-bg flex items-center gap-4 border border-border px-3 py-2">
        <MiniLabel>
          activity · spans per {BUCKET_S} s · last {BUCKETS * BUCKET_S} s
        </MiniLabel>
        <div className="otel-w-44">
          <Sparkline values={buckets} label={`spans finished per ${BUCKET_S} seconds over the last ${BUCKETS * BUCKET_S} seconds`} />
        </div>
        <div className="ml-auto flex flex-wrap gap-3">
          {(Object.keys(stats.byKind) as Kind[])
            .sort((a, b) => (stats.byKind[b] || 0) - (stats.byKind[a] || 0))
            .slice(0, 7)
            .map((k) => (
              <span
                key={k}
                className="inline-flex items-center gap-1.5 text-[11px] text-muted-foreground"
                title={`${stats.byKind[k]} turn${stats.byKind[k] === 1 ? "" : "s"} rooted in a ${k} span`}
              >
                <span className="otel-w-2 inline-block h-2 rounded-full" style={{ background: KIND_COLOR[k] }} aria-hidden />
                {k} {stats.byKind[k]}
              </span>
            ))}
        </div>
      </div>

      <div className="flex items-center justify-between pt-1">
        <MiniLabel>recent turns</MiniLabel>
        <div className="flex items-center gap-3 text-[11px] text-muted-foreground">
          <Toggle
            checked={showPings}
            onChange={setShowPings}
            label={`show MCP keepalive pings${hiddenPings ? ` (${hiddenPings} hidden)` : ""}`}
            Switch={Checkbox}
          />
          <span>open a turn to see its span waterfall</span>
        </div>
      </div>

      <div className="flex flex-col gap-2">
        {shown.length === 0 && loaded ? (
          <Empty title="Waiting for activity…">
            Run a Hermes turn (CLI, Telegram, anything). Each turn appears here as a card — open it to see every span, timing and attribute. No backend
            required.
          </Empty>
        ) : (
          shown.map((t) => <TraceCard key={t.traceId} row={t} onSelect={(r) => setSelected(r.raw as LiveTrace)} />)
        )}
      </div>
    </div>
  );
}
