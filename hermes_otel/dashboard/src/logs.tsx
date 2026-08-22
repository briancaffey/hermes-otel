import { React, useState, useEffect, useRef, useCallback, fetchJSON, API, Button, Input, Select, SelectOption, cn } from "./sdk";
import { fmtTimeAgo } from "./lib";

/* eslint-disable @typescript-eslint/no-explicit-any */
type LogRec = {
  seq: number;
  time_unix_nano?: number;
  level?: string;
  logger?: string;
  body?: string;
  trace_id?: string;
  attributes?: Record<string, any>;
};
const POLL_MS = 1500;
const MAX_KEEP = 1000;

const LEVEL_CLASS: Record<string, string> = {
  ERROR: "text-destructive",
  WARNING: "text-amber-400",
  WARN: "text-amber-400",
  INFO: "text-sky-400",
  DEBUG: "text-muted-foreground",
};
const SEVERITY: Record<string, number> = { DEBUG: 10, INFO: 20, WARNING: 30, WARN: 30, ERROR: 40, CRITICAL: 50 };

export function LogsPage() {
  const [logs, setLogs] = useState<LogRec[]>([]);
  const [live, setLive] = useState<boolean | null>(null);
  const [paused, setPaused] = useState(false);
  const [filter, setFilter] = useState("");
  const [minLevel, setMinLevel] = useState("0");
  const cursor = useRef(0);

  const poll = useCallback(async () => {
    try {
      const st = await fetchJSON(`${API}/live/status`);
      setLive(st && st.live !== false);
      if (!st || st.live === false) return;
      const r = await fetchJSON(`${API}/live/logs?since=${cursor.current}&limit=2000`);
      cursor.current = Math.max(r.cursor || 0, cursor.current);
      if (r.logs?.length) setLogs((prev) => [...prev, ...r.logs].slice(-MAX_KEEP));
    } catch {
      /* keep last */
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

  const f = filter.trim().toLowerCase();
  const min = Number(minLevel);
  const shown = logs
    .slice()
    .reverse()
    .filter((l) => (SEVERITY[(l.level || "INFO").toUpperCase()] || 20) >= min)
    .filter((l) => !f || (l.body || "").toLowerCase().includes(f) || (l.logger || "").toLowerCase().includes(f))
    .slice(0, 300);

  if (live === false || (live && logs.length === 0))
    return (
      <div className="space-y-3">
        <div className="flex items-center gap-2">
          <span className="text-base font-semibold tracking-tight">Logs</span>
          <span className="text-xs text-muted-foreground">in-process tail</span>
        </div>
        <div className="border border-dashed border-border px-4 py-12 text-center text-sm text-muted-foreground">
          <div className="mb-1 text-base font-medium text-foreground">No logs captured</div>
          Set <span className="font-mono">capture_logs: true</span> in the plugin config and run a turn — the agent's
          log lines (correlated to their trace) stream here.
        </div>
      </div>
    );

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <span className="text-base font-semibold tracking-tight">Logs</span>
          <span className="text-xs text-muted-foreground">{logs.length} buffered</span>
        </div>
        <div className="flex items-center gap-2">
          <Select value={minLevel} onValueChange={setMinLevel}>
            <SelectOption value="0">All levels</SelectOption>
            <SelectOption value="20">Info+</SelectOption>
            <SelectOption value="30">Warn+</SelectOption>
            <SelectOption value="40">Error</SelectOption>
          </Select>
          <Input placeholder="filter…" value={filter} onChange={(e: any) => setFilter(e.target.value)} className="h-8 w-40" />
          <Button variant="outline" size="sm" onClick={() => setPaused((p) => !p)}>
            {paused ? "▶" : "⏸"}
          </Button>
        </div>
      </div>
      <div className="overflow-hidden border border-border bg-card/40 font-mono text-xs">
        {shown.map((l) => {
          const lvl = (l.level || "INFO").toUpperCase();
          return (
            <div key={l.seq} className="flex items-start gap-2 border-b border-border/60 px-3 py-1 last:border-b-0">
              <span className="w-14 shrink-0 text-muted-foreground/70">
                {l.time_unix_nano ? fmtTimeAgo(l.time_unix_nano) : ""}
              </span>
              <span className={cn("w-12 shrink-0 font-semibold", LEVEL_CLASS[lvl] || "text-muted-foreground")}>{lvl}</span>
              {l.logger ? <span className="w-40 shrink-0 truncate text-muted-foreground" title={l.logger}>{l.logger}</span> : null}
              <span className="min-w-0 flex-1 whitespace-pre-wrap break-words text-foreground/90">{l.body}</span>
              {l.trace_id ? <span className="shrink-0 font-mono text-[10px] text-muted-foreground/60">{String(l.trace_id).slice(0, 8)}</span> : null}
            </div>
          );
        })}
      </div>
    </div>
  );
}
