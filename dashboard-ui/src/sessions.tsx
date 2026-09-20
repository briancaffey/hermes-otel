// Sessions view: one row per session id, expandable into its turns (#187).
import { React, useState, useEffect, useCallback, fetchJSON, API, Badge, Button, cn } from "./sdk";
import { SessionRow, LiveTrace, fmtDurationMs, fmtTokens, fmtTimeAgo, fmtAbsTime, fmtInt, groupBySession } from "./lib";
import { liveParams, TraceFilters } from "./params";
import { LiveTraceCard } from "./spantree";
import { ErrorBanner } from "./atoms";

function SessionCard({ row, open, onToggle, children }: { row: SessionRow; open: boolean; onToggle: () => void; children?: any }) {
  return (
    <div className={cn("otel-card-bg border", row.errors ? "border-destructive/30" : "border-border")}>
      <div className="flex cursor-pointer flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2" onClick={onToggle} role="button" tabIndex={0}>
        <span className="w-3 shrink-0 text-xs text-muted-foreground">{open ? "▾" : "▸"}</span>
        <span className="font-mono text-sm" title={row.session}>
          {row.session}
        </span>
        {row.platform ? <Badge variant="secondary" className="text-[10px]">{row.platform}</Badge> : null}
        {row.errors ? <Badge variant="destructive" className="text-[10px]">{row.errors} error{row.errors === 1 ? "" : "s"}</Badge> : null}
        <span className="ml-auto flex flex-wrap items-center gap-x-3 text-xs text-muted-foreground">
          {row.model ? <span className="font-mono text-foreground/80">{row.model}</span> : null}
          <span className="tabular-nums">{row.turns} turn{row.turns === 1 ? "" : "s"}</span>
          {row.spans != null ? <span className="tabular-nums">{row.spans} spans</span> : null}
          {row.toolCalls != null ? <span className="tabular-nums">{row.toolCalls} tool calls</span> : null}
          {row.tokens != null ? <span className="tabular-nums">{fmtTokens(row.tokens)} tok</span> : null}
          {row.cost != null ? <span className="tabular-nums text-emerald-400">${row.cost.toFixed(4)}</span> : null}
          <span className="tabular-nums">{fmtDurationMs((row.endNs - row.startNs) / 1e6)}</span>
          <span title={`${fmtAbsTime(row.startNs)} → ${fmtAbsTime(row.endNs)}`}>{fmtTimeAgo(row.endNs)}</span>
        </span>
      </div>
      {open ? <div className="border-t border-border/60 px-3 py-2">{children}</div> : null}
    </div>
  );
}

// Live source: rows from /live/sessions, turns from /live/traces?session=.
export function LiveSessions({ filters, onSelectTrace }: { filters: TraceFilters; onSelectTrace: (t: LiveTrace) => void }) {
  const [rows, setRows] = useState<SessionRow[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [turns, setTurns] = useState<Record<string, LiveTrace[]>>({});

  const load = useCallback(async () => {
    try {
      const r = await fetchJSON(`${API}/live/sessions?lookback_hours=${filters.lookback}&limit=100`);
      setRows(r.sessions || []);
      setError(null);
    } catch (e: any) {
      setError(String(e?.message || e));
    }
  }, [filters.lookback]);
  useEffect(() => {
    load();
  }, [load]);

  const toggle = async (sid: string) => {
    if (open === sid) return setOpen(null);
    setOpen(sid);
    if (!turns[sid]) {
      const p = liveParams({ ...filters, session: sid }, 200);
      try {
        const r = await fetchJSON(`${API}/live/traces?${p}`);
        setTurns((prev) => ({ ...prev, [sid]: r.traces || [] }));
      } catch {
        setTurns((prev) => ({ ...prev, [sid]: [] }));
      }
    }
  };

  if (error) return <ErrorBanner error={error} />;
  if (!rows.length)
    return (
      <div className="border border-dashed border-border px-4 py-8 text-center text-sm text-muted-foreground">
        No sessions in the last {filters.lookback}h. Turns carry <span className="font-mono">hermes.session_id</span>; sessions group them.
      </div>
    );
  return (
    <div className="flex flex-col gap-2">
      <div className="text-xs text-muted-foreground">{rows.length} session{rows.length === 1 ? "" : "s"} · click one to see its turns in order</div>
      {rows.map((row) => (
        <SessionCard key={row.session} row={row} open={open === row.session} onToggle={() => toggle(row.session)}>
          {turns[row.session] ? (
            turns[row.session].length ? (
              <div className="flex flex-col gap-1.5">
                {turns[row.session]
                  .slice()
                  .sort((a, b) => a.startNs - b.startNs)
                  .map((t) => (
                    <LiveTraceCard key={t.traceId} trace={t} onSelect={onSelectTrace} />
                  ))}
              </div>
            ) : (
              <div className="text-xs text-muted-foreground">No turns matched the current filters.</div>
            )
          ) : (
            <div className="text-xs text-muted-foreground">Loading…</div>
          )}
        </SessionCard>
      ))}
    </div>
  );
}

// Backend source: the search results grouped client-side by the session id the
// adapter put on each card.
export function BackendSessions({ traces, renderTrace }: { traces: any[]; renderTrace: (t: any) => any }) {
  const [open, setOpen] = useState<string | null>(null);
  const rows = groupBySession(traces);
  const unattributed = traces.length - rows.reduce((n, r) => n + r.turns, 0);
  if (!traces.length) return null;
  if (!rows.length)
    return (
      <div className="border border-dashed border-border px-4 py-8 text-center text-sm text-muted-foreground">
        None of the {traces.length} results carries a session id, so they cannot be grouped. The Turns view lists them.
      </div>
    );
  const byId: Record<string, any> = {};
  for (const t of traces) byId[t.traceID || t.traceId] = t;
  return (
    <div className="flex flex-col gap-2">
      <div className="text-xs text-muted-foreground">
        {rows.length} session{rows.length === 1 ? "" : "s"} from {traces.length} results{unattributed ? ` · ${unattributed} without a session id` : ""}
      </div>
      {rows.map((row) => (
        <SessionCard key={row.session} row={row} open={open === row.session} onToggle={() => setOpen(open === row.session ? null : row.session)}>
          <div className="flex flex-col gap-1.5">
            {row.traceIds
              .map((id) => byId[id])
              .filter(Boolean)
              .sort((a: any, b: any) => Number(a.startTimeUnixNano || 0) - Number(b.startTimeUnixNano || 0))
              .map((t: any) => renderTrace(t))}
          </div>
        </SessionCard>
      ))}
    </div>
  );
}

export function ViewToggle({ view, onChange }: { view: "turns" | "sessions"; onChange: (v: "turns" | "sessions") => void }) {
  const Btn = ({ id, label }: { id: "turns" | "sessions"; label: string }) => (
    <button
      type="button"
      onClick={() => onChange(id)}
      className={cn("otel-toggle px-3 py-1 text-xs font-medium transition-colors", view === id ? "otel-toggle-active text-foreground" : "text-muted-foreground hover:text-foreground")}
    >
      {label}
    </button>
  );
  return (
    <div className="otel-card-bg inline-flex border border-border p-0.5">
      <Btn id="turns" label="Turns" />
      <Btn id="sessions" label="Sessions" />
    </div>
  );
}

export { fmtInt, Button };
