import { React, useState, useEffect, useRef, useCallback, fetchJSON, API, Badge, Button } from "./sdk";
import {
  LiveSpan,
  spanKind,
  KIND_COLOR,
  KIND_LABEL,
  Kind,
  spanCost,
  spanTokens,
  spanModel,
  sessionOf,
  fmtCost,
  fmtInt,
  fmtDuration,
  fmtAgo,
} from "./lib";
import { Stat, KindDot, Sparkline, Pulse, EmptyState, ErrorBanner } from "./ui";

const POLL_MS = 1500;
const MAX_KEEP = 600; // cap accumulated spans in the browser

function deriveStats(spans: LiveSpan[]) {
  let cost = 0;
  let tokens = 0;
  let errors = 0;
  const traces = new Set<string>();
  const byKind: Record<string, number> = {};
  for (const s of spans) {
    cost += spanCost(s) || 0;
    tokens += spanTokens(s) || 0;
    if (s.status === "ERROR") errors++;
    traces.add(s.trace_id);
    const k = spanKind(s);
    byKind[k] = (byKind[k] || 0) + 1;
  }
  return { cost, tokens, errors, traces: traces.size, byKind };
}

function SpanRow({ s }: { s: LiveSpan }) {
  const kind: Kind = spanKind(s);
  const model = spanModel(s);
  const cost = spanCost(s);
  const tokens = spanTokens(s);
  const approvalChoice = s.attributes["hermes.approval.choice"];
  const skillName = s.attributes["hermes.skill.name"];
  return (
    <div className="otel-span-row" style={{ borderLeftColor: KIND_COLOR[kind] }}>
      <div className="otel-span-main">
        <KindDot kind={kind} />
        <span className="otel-span-name" title={s.name}>
          {s.name}
        </span>
        <span className="otel-span-meta">
          {s.status === "ERROR" ? <Badge variant="destructive">error</Badge> : null}
          {model ? <span className="otel-chip">{model}</span> : null}
          {tokens ? <span className="otel-chip">{fmtInt(tokens)} tok</span> : null}
          {cost ? <span className="otel-chip otel-chip-cost">{fmtCost(cost)}</span> : null}
          {approvalChoice ? <span className="otel-chip">👤 {approvalChoice}</span> : null}
          {skillName && kind === "skill" ? <span className="otel-chip">🧩 {skillName}</span> : null}
        </span>
      </div>
      <div className="otel-span-side">
        <span className="otel-span-dur">{fmtDuration(s.duration_ms)}</span>
        <span className="otel-span-ago">{fmtAgo(s.end_time_unix_nano || s.start_time_unix_nano)}</span>
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
      if (sp.spans && sp.spans.length) {
        setSpans((prev) => [...prev, ...sp.spans].slice(-MAX_KEEP));
      }
      setError(null);
    } catch (e: any) {
      setError(String(e && e.message ? e.message : e));
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
  const recent = spans.slice().reverse().slice(0, 120);
  const lastSession = spans.length ? sessionOf(spans[spans.length - 1]) : null;

  // spans-per-2s activity sparkline (last ~90s)
  const now = Date.now();
  const buckets = new Array(45).fill(0);
  for (const s of spans) {
    const t = (s.end_time_unix_nano || s.start_time_unix_nano) / 1e6;
    const idx = 44 - Math.floor((now - t) / 2000);
    if (idx >= 0 && idx < 45) buckets[idx]++;
  }

  if (status && status.live === false) {
    return (
      <EmptyState
        title="Live mode is off"
        hint={
          status.reason ||
          "Set dashboard_live: true in the plugin config.yaml (it's on by default), then run a turn."
        }
      />
    );
  }

  return (
    <div className="otel-live">
      <div className="otel-live-header">
        <div className="otel-live-title">
          <Pulse active={!paused && (status?.spans || 0) > 0} />
          <span>Live</span>
          <span className="otel-live-sub">
            in-process · {fmtInt(status?.spans)} spans buffered
            {lastSession ? <> · session <code>{String(lastSession).slice(0, 16)}</code></> : null}
          </span>
        </div>
        <Button variant="outline" size="sm" onClick={() => setPaused((p: boolean) => !p)}>
          {paused ? "▶ Resume" : "⏸ Pause"}
        </Button>
      </div>

      {error ? <ErrorBanner error={error} /> : null}

      <div className="otel-stat-grid">
        <Stat label="Cost" value={fmtCost(stats.cost)} accent="cost" />
        <Stat label="Tokens" value={fmtInt(stats.tokens)} />
        <Stat label="Traces" value={fmtInt(stats.traces)} />
        <Stat label="Spans" value={fmtInt(spans.length)} />
        <Stat label="Errors" value={fmtInt(stats.errors)} accent={stats.errors ? "error" : undefined} />
      </div>

      <div className="otel-activity">
        <span className="otel-activity-label">activity</span>
        <Sparkline values={buckets} />
        <div className="otel-kind-legend">
          {(Object.keys(stats.byKind) as Kind[])
            .sort((a, b) => stats.byKind[b] - stats.byKind[a])
            .slice(0, 7)
            .map((k) => (
              <span className="otel-legend-item" key={k}>
                <KindDot kind={k} />
                {KIND_LABEL[k]} {stats.byKind[k]}
              </span>
            ))}
        </div>
      </div>

      <div className="otel-stream">
        {recent.length === 0 ? (
          <EmptyState
            title="Waiting for activity…"
            hint="Run a Hermes turn (CLI, Telegram, anything). Spans appear here in real time — no backend needed."
          />
        ) : (
          recent.map((s) => <SpanRow key={s.seq} s={s} />)
        )}
      </div>
    </div>
  );
}
