import { React, useState, useEffect, useRef, useCallback, fetchJSON, API, Button } from "./sdk";
import {
  LiveSpan,
  LiveTrace,
  kindOf,
  Kind,
  KIND_HEX,
  liveCost,
  liveTokens,
  sessionOf,
  groupLiveTraces,
  liveTreeFromSpans,
  fmtCost,
  fmtInt,
} from "./lib";
import { Stat, Sparkline, Pulse, MiniLabel, ErrorBanner } from "./atoms";
import { LiveTraceCard, LiveTraceDetail } from "./spantree";

/* eslint-disable @typescript-eslint/no-explicit-any */
const POLL_MS = 1500;
const MAX_KEEP = 1500;

function deriveStats(spans: LiveSpan[]) {
  let cost = 0;
  let tokens = 0;
  let errors = 0;
  const traces = new Set<string>();
  const byKind: Record<string, number> = {};
  for (const s of spans) {
    cost += liveCost(s) || 0;
    tokens += liveTokens(s) || 0;
    if (s.status === "ERROR") errors++;
    traces.add(s.trace_id);
    const k = kindOf(s.name, s.attributes);
    byKind[k] = (byKind[k] || 0) + 1;
  }
  return { cost, tokens, errors, traces: traces.size, byKind };
}

export function LivePage() {
  const [spans, setSpans] = useState<LiveSpan[]>([]);
  const [status, setStatus] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);
  const [paused, setPaused] = useState(false);
  const [selected, setSelected] = useState<LiveTrace | null>(null);
  const cursor = useRef(0);

  const poll = useCallback(async () => {
    try {
      const st = await fetchJSON(`${API}/live/status`);
      setStatus(st);
      if (!st || st.live === false) return;
      const sp = await fetchJSON(`${API}/live/spans?since=${cursor.current}&limit=2000`);
      cursor.current = Math.max(sp.cursor || 0, cursor.current);
      if (sp.spans?.length) setSpans((prev) => [...prev, ...sp.spans].slice(-MAX_KEEP));
      setError(null);
    } catch (e: any) {
      setError(String(e?.message || e));
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

  const stats = deriveStats(spans);
  const traces = groupLiveTraces(spans);
  const lastSession = spans.length ? sessionOf(spans[spans.length - 1]) : null;

  const now = Date.now();
  const buckets = new Array(50).fill(0);
  for (const s of spans) {
    const t = (s.end_time_unix_nano || s.start_time_unix_nano) / 1e6;
    const idx = 49 - Math.floor((now - t) / 2000);
    if (idx >= 0 && idx < 50) buckets[idx]++;
  }

  if (status && status.live === false) {
    return (
      <div className="border border-dashed border-border px-4 py-12 text-center text-sm text-muted-foreground">
        <div className="mb-1 text-base font-medium text-foreground">Live mode is off</div>
        {status.reason || "Set dashboard_live: true in the plugin config (it's on by default), then run a turn."}
      </div>
    );
  }

  // Detail view — full waterfall for the picked turn.
  if (selected) {
    const fresh = groupLiveTraces(spans).find((t) => t.traceId === selected.traceId) || selected;
    const { roots } = liveTreeFromSpans(fresh.spans);
    return (
      <div className="space-y-3">
        <LiveTraceDetail trace={fresh} roots={roots} onBack={() => setSelected(null)} />
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
        <Button variant="outline" size="sm" onClick={() => setPaused((p) => !p)}>
          {paused ? "▶ Resume" : "⏸ Pause"}
        </Button>
      </div>

      {error ? <ErrorBanner error={error} /> : null}

      <div className="grid grid-cols-2 gap-2 sm:grid-cols-5">
        <Stat label="Cost" value={fmtCost(stats.cost)} accent="cost" />
        <Stat label="Tokens" value={fmtInt(stats.tokens)} />
        <Stat label="Turns" value={fmtInt(stats.traces)} />
        <Stat label="Spans" value={fmtInt(spans.length)} />
        <Stat label="Errors" value={fmtInt(stats.errors)} accent={stats.errors ? "error" : undefined} />
      </div>

      <div className="flex items-center gap-4 border border-border bg-card/40 px-3 py-2">
        <MiniLabel>activity</MiniLabel>
        <div className="w-44">
          <Sparkline values={buckets} />
        </div>
        <div className="ml-auto flex flex-wrap gap-3">
          {(Object.keys(stats.byKind) as Kind[])
            .sort((a, b) => stats.byKind[b] - stats.byKind[a])
            .slice(0, 7)
            .map((k) => (
              <span key={k} className="inline-flex items-center gap-1.5 text-[11px] text-muted-foreground">
                <span className="inline-block h-2 w-2 rounded-full" style={{ background: KIND_HEX[k] }} />
                {k} {stats.byKind[k]}
              </span>
            ))}
        </div>
      </div>

      <div className="flex items-center justify-between pt-1">
        <MiniLabel>recent turns</MiniLabel>
        <span className="text-[11px] text-muted-foreground">click a turn to open its span waterfall</span>
      </div>

      <div className="flex flex-col gap-2">
        {traces.length === 0 ? (
          <div className="border border-dashed border-border px-4 py-12 text-center text-sm text-muted-foreground">
            <div className="mb-1 text-base font-medium text-foreground">Waiting for activity…</div>
            Run a Hermes turn (CLI, Telegram, anything). Each turn appears here as a card — open it to see every span,
            timing and attribute. No backend required.
          </div>
        ) : (
          traces.map((t) => <LiveTraceCard key={t.traceId} trace={t} onSelect={setSelected} />)
        )}
      </div>
    </div>
  );
}
