// Sessions view: one row per session id, expandable into its turns (#187, #282).
import { React, useState, useEffect, useCallback, api, Badge, Button } from "./sdk";
import { SessionRow, LiveTrace, TraceRow, fmtDurationMs, fmtTokens, fmtCostExact, fmtTimeAgo, fmtAbsTime, groupBySession } from "./lib";
import { liveParams, TraceFilters } from "./params";
import { TraceCard } from "./spantree";
import { ErrorBanner, Clickable, Empty } from "./atoms";
import { usePolling } from "./poll";
import { navigate } from "./nav";

const POLL_MS = 10000;
const PAGE = 50;

function SessionCard({ row, open, onToggle, children, partial }: { row: SessionRow; open: boolean; onToggle: () => void; children?: any; partial?: boolean }) {
  const lookback = Math.max(1, Math.ceil((Date.now() - row.startNs / 1e6) / 3600000) + 1);
  return (
    <div className={row.errors ? "otel-card-bg border border-destructive/30" : "otel-card-bg border border-border"}>
      <Clickable
        onActivate={onToggle}
        aria-expanded={open}
        label={`${open ? "collapse" : "expand"} session ${row.session}`}
        className="otel-row flex cursor-pointer flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2"
      >
        <span className="w-3 shrink-0 text-xs text-muted-foreground" aria-hidden>
          {open ? "▾" : "▸"}
        </span>
        <span className="font-mono text-sm" title={row.session}>
          {row.session}
        </span>
        {row.platform ? (
          <Badge variant="secondary" className="text-[10px]">
            {row.platform}
          </Badge>
        ) : null}
        {row.errors ? (
          <Badge variant="destructive" className="text-[10px]">
            {row.errors} error{row.errors === 1 ? "" : "s"}
          </Badge>
        ) : null}
        {partial ? (
          <Badge variant="secondary" className="text-[10px]" title="grouped from the traces on this page of results, not the whole session">
            this page only
          </Badge>
        ) : null}
        <span className="ml-auto flex flex-wrap items-center gap-x-3 text-xs text-muted-foreground">
          {row.model ? <span className="font-mono text-foreground/80">{row.model}</span> : null}
          <span className="tabular-nums">
            {row.turns} turn{row.turns === 1 ? "" : "s"}
          </span>
          {row.spans != null ? <span className="tabular-nums">{row.spans} spans</span> : null}
          {row.toolCalls != null ? <span className="tabular-nums">{row.toolCalls} tool calls</span> : null}
          {row.tokens != null ? <span className="tabular-nums">{fmtTokens(row.tokens)} tok</span> : null}
          {row.cost != null ? <span className="tabular-nums otel-c-cost">{fmtCostExact(row.cost)}</span> : null}
          <span className="tabular-nums" title="wall time from the first turn's start to the last turn's end">
            {fmtDurationMs((row.endNs - row.startNs) / 1e6)} span
          </span>
          <button
            type="button"
            className="otel-link text-[11px]"
            title="this session's log lines and events"
            onClick={(e: any) => {
              e.stopPropagation();
              navigate({ tab: "logs", session: row.session, trace: "", lookback: String(Math.min(720, lookback)) });
            }}
          >
            logs
          </button>
          <span title={`${fmtAbsTime(row.startNs)} → ${fmtAbsTime(row.endNs)}`}>{fmtTimeAgo(row.endNs)}</span>
        </span>
      </Clickable>
      {open ? <div className="border-t border-border/60 px-3 py-2">{children}</div> : null}
    </div>
  );
}

// Live source: rows from /live/sessions (lookback + limit), turns from
// /live/traces?session=. The view keeps only the lookback of the search bar
// (the session query does not take the other fields), polls like the Turns
// view, and opens the session a link asked for.
export function LiveSessions({
  filters,
  wantedSession,
  onSelectTrace,
  active,
}: {
  filters: TraceFilters;
  wantedSession: string;
  onSelectTrace: (t: LiveTrace) => void;
  active: boolean;
}) {
  const [rows, setRows] = useState<SessionRow[]>([]);
  const [hasMore, setHasMore] = useState(false);
  const [limit, setLimit] = useState(PAGE);
  const [error, setError] = useState<unknown>(null);
  const [open, setOpen] = useState<string | null>(wantedSession || null);
  const [turns, setTurns] = useState<Record<string, LiveTrace[]>>({});
  const [loaded, setLoaded] = useState(false);

  const load = useCallback(async () => {
    try {
      const r = await api("/live/sessions", { lookback_hours: filters.lookback, limit });
      setRows(r.sessions || []);
      setHasMore(!!r.has_more);
      setError(null);
    } catch (e: unknown) {
      setError(e);
    } finally {
      setLoaded(true);
    }
  }, [filters.lookback, limit]);
  useEffect(() => {
    load();
  }, [load]);
  usePolling(load, POLL_MS, active);

  const loadTurns = useCallback(
    async (sid: string) => {
      const p = liveParams({ ...filters, session: sid }, 200);
      try {
        const r = await api("/live/traces", p);
        setTurns((prev) => ({ ...prev, [sid]: r.traces || [] }));
      } catch {
        setTurns((prev) => ({ ...prev, [sid]: [] }));
      }
    },
    [filters]
  );
  // The open session's turns follow the poll too.
  useEffect(() => {
    if (open) loadTurns(open);
  }, [open, loadTurns, rows]);

  const toggle = (sid: string) => setOpen((o) => (o === sid ? null : sid));

  if (error) return <ErrorBanner error={error} prefix="Sessions" />;
  const shown = wantedSession ? rows.filter((r) => r.session === wantedSession) : rows;
  if (loaded && !shown.length)
    return (
      <Empty title={wantedSession ? `Session ${wantedSession} is not in the last ${filters.lookback}h` : `No sessions in the last ${filters.lookback}h`}>
        Turns carry <span className="font-mono">hermes.session_id</span>; sessions group them.
        {wantedSession ? (
          <div className="mt-2">
            <Button variant="outline" size="sm" onClick={() => navigate({ tab: "traces", view: "sessions", session: "", trace: "" })}>
              show every session
            </Button>
          </div>
        ) : null}
      </Empty>
    );
  return (
    <div className="flex flex-col gap-2">
      <div className="text-xs text-muted-foreground">
        {wantedSession ? (
          <>
            one session ·{" "}
            <button type="button" className="otel-link" onClick={() => navigate({ tab: "traces", view: "sessions", session: "", trace: "" })}>
              show every session
            </button>
          </>
        ) : (
          `${rows.length} session${rows.length === 1 ? "" : "s"} · click one to see its turns in order`
        )}
      </div>
      {shown.map((row) => (
        <SessionCard key={row.session} row={row} open={open === row.session} onToggle={() => toggle(row.session)}>
          {turns[row.session] ? (
            turns[row.session].length ? (
              <div className="flex flex-col gap-1.5">
                {turns[row.session]
                  .slice()
                  .sort((a, b) => a.startNs - b.startNs)
                  .map((t) => (
                    <TraceCard key={t.traceId} row={{ ...rowFromLiveLocal(t) }} onSelect={() => onSelectTrace(t)} />
                  ))}
                {turns[row.session].length >= 200 ? <div className="text-xs text-muted-foreground">Showing the first 200 turns of this session.</div> : null}
              </div>
            ) : (
              <div className="text-xs text-muted-foreground">No turns in this window.</div>
            )
          ) : (
            <div className="text-xs text-muted-foreground">Loading…</div>
          )}
        </SessionCard>
      ))}
      {hasMore && !wantedSession ? (
        <div className="flex justify-center">
          <Button variant="outline" size="sm" onClick={() => setLimit((n) => n + PAGE)}>
            Show more sessions
          </Button>
        </div>
      ) : null}
    </div>
  );
}

// A local alias so sessions.tsx does not import spantree's LiveTraceCard back.
import { rowFromLive as rowFromLiveLocal } from "./lib";

// Backend source: the page of search results grouped client-side by the
// session id the adapter put on each card. The rows say so: they are not
// whole-session aggregates (#282).
export function BackendSessions({ rows, wantedSession, renderTrace }: { rows: TraceRow[]; wantedSession: string; renderTrace: (t: TraceRow) => any }) {
  const [open, setOpen] = useState<string | null>(wantedSession || null);
  const grouped = groupBySession(rows.map((r) => r.raw));
  const unattributed = rows.length - grouped.reduce((n, r) => n + r.turns, 0);
  if (!rows.length) return null;
  if (!grouped.length)
    return (
      <div className="border border-dashed border-border px-4 py-8 text-center text-sm text-muted-foreground">
        None of the {rows.length} results carries a session id, so they cannot be grouped. The Turns view lists them.
      </div>
    );
  const byId: Record<string, TraceRow> = {};
  for (const t of rows) byId[t.traceId] = t;
  const shown = wantedSession ? grouped.filter((r) => r.session === wantedSession) : grouped;
  return (
    <div className="flex flex-col gap-2">
      <div className="text-xs text-muted-foreground">
        {shown.length} session{shown.length === 1 ? "" : "s"} grouped from the {rows.length} results on this page
        {unattributed ? ` · ${unattributed} without a session id` : ""}
        {wantedSession ? (
          <>
            {" · "}
            <button type="button" className="otel-link" onClick={() => navigate({ tab: "traces", view: "sessions", session: "", trace: "" })}>
              show every session
            </button>
          </>
        ) : null}
      </div>
      {shown.map((row) => (
        <SessionCard key={row.session} row={row} open={open === row.session} partial onToggle={() => setOpen(open === row.session ? null : row.session)}>
          <div className="flex flex-col gap-1.5">
            {row.traceIds
              .map((id) => byId[id])
              .filter(Boolean)
              .sort((a, b) => a.startNs - b.startNs)
              .map((t) => renderTrace(t))}
          </div>
        </SessionCard>
      ))}
    </div>
  );
}
