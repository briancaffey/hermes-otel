import { React, useState, useEffect, useRef, useCallback, fetchJSON, API, Badge, Button } from "./sdk";
import {
  LiveSpan,
  kindOf,
  Kind,
  KIND_HEX,
  liveCost,
  liveTokens,
  liveModel,
  sessionOf,
  fmtCost,
  fmtInt,
  fmtDurationMs,
  fmtTimeAgo,
} from "./lib";
import { Stat, Sparkline, Pulse, MiniLabel, ErrorBanner } from "./atoms";

/* eslint-disable @typescript-eslint/no-explicit-any */
const POLL_MS = 1500;
const MAX_KEEP = 800;

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

function StreamRow({ s }: { s: LiveSpan }) {
  const kind = kindOf(s.name, s.attributes);
  const model = liveModel(s);
  const cost = liveCost(s);
  const tokens = liveTokens(s);
  const approval = s.attributes["hermes.approval.choice"];
  const skill = s.attributes["hermes.skill.name"];
  return (
    <div
      className="flex items-center justify-between gap-3 border border-border bg-card/40 px-3 py-1.5 otel-slidein"
      style={{ borderLeftWidth: 3, borderLeftColor: KIND_HEX[kind] }}
    >
      <div className="flex min-w-0 flex-1 items-center gap-2">
        <span className="inline-block h-2 w-2 shrink-0 rounded-full" style={{ background: KIND_HEX[kind] }} />
        <span className="truncate font-mono text-sm" title={s.name}>
          {s.name}
        </span>
        <span className="flex flex-wrap items-center gap-1.5">
          {s.status === "ERROR" ? <Badge variant="destructive" className="text-[10px]">error</Badge> : null}
          {model ? <span className="font-mono text-[11px] text-muted-foreground">{model}</span> : null}
          {tokens ? <Badge variant="secondary" className="text-[10px] tabular-nums">{fmtInt(tokens)} tok</Badge> : null}
          {cost ? <span className="tabular-nums text-[11px] text-emerald-400">{fmtCost(cost)}</span> : null}
          {approval ? <Badge variant="secondary" className="text-[10px]">👤 {approval}</Badge> : null}
          {skill && kind === "skill" ? <Badge variant="secondary" className="text-[10px]">🧩 {skill}</Badge> : null}
        </span>
      </div>
      <div className="flex shrink-0 flex-col items-end">
        <span className="tabular-nums text-xs font-medium">{fmtDurationMs(s.duration_ms)}</span>
        <span className="text-[11px] text-muted-foreground">{fmtTimeAgo(s.end_time_unix_nano || s.start_time_unix_nano)}</span>
      </div>
    </div>
  );
}

export function LivePage() {
  const [spans, setSpans] = useState<LiveSpan[]>([]);
  const [status, setStatus] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);
  const [paused, setPaused] = useState(false);
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
    if (paused) return;
    const id = setInterval(poll, POLL_MS);
    return () => clearInterval(id);
  }, [poll, paused]);

  const stats = deriveStats(spans);
  const recent = spans.slice().reverse().slice(0, 150);
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

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2.5">
          <Pulse active={!paused && (status?.spans || 0) > 0} />
          <span className="text-base font-semibold tracking-tight">Live</span>
          <span className="text-xs text-muted-foreground">
            in-process · {fmtInt(status?.spans)} spans buffered
            {lastSession ? (
              <>
                {" "}
                · session <span className="font-mono">{String(lastSession).slice(0, 16)}</span>
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
        <Stat label="Traces" value={fmtInt(stats.traces)} />
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

      <div className="flex flex-col gap-1.5">
        {recent.length === 0 ? (
          <div className="border border-dashed border-border px-4 py-12 text-center text-sm text-muted-foreground">
            <div className="mb-1 text-base font-medium text-foreground">Waiting for activity…</div>
            Run a Hermes turn (CLI, Telegram, anything). Spans stream in here live — no backend required.
          </div>
        ) : (
          recent.map((s) => <StreamRow key={s.seq} s={s} />)
        )}
      </div>
    </div>
  );
}
