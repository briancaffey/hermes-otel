// Shared span-tree waterfall + trace cards. Used by both the Traces browser and
// the Live "recent turns" feed so live spans and backend spans render identically.
import { React, useState, useMemo, Card, CardHeader, CardTitle, CardContent, Badge, Button, cn } from "./sdk";
import {
  fmtDurationMs,
  fmtTokens,
  fmtTimeAgo,
  fmtAbsTime,
  kindOf,
  statusCode,
  KIND_HEX,
  TreeSpan,
  flatten,
  LiveTrace,
} from "./lib";
import { kindIcon, IconChevronRight } from "./icons";

/* eslint-disable @typescript-eslint/no-explicit-any */

export function AttrTable({ attrs }: { attrs: Record<string, any> }) {
  const keys = Object.keys(attrs || {}).sort();
  if (!keys.length) return <div className="text-xs text-muted-foreground">(no attributes)</div>;
  return (
    <dl className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1 text-xs">
      {keys.map((k) => {
        const v = attrs[k];
        const rendered = v && typeof v === "object" ? JSON.stringify(v, null, 2) : String(v);
        return (
          <React.Fragment key={k}>
            <dt className="text-muted-foreground">{k}</dt>
            <dd className="whitespace-pre-wrap break-words text-foreground">{rendered}</dd>
          </React.Fragment>
        );
      })}
    </dl>
  );
}

function SpanRow({ span, depth, open, onToggle, t0, total }: { span: TreeSpan; depth: number; open: boolean; onToggle: () => void; t0: number; total: number }) {
  const kind = kindOf(span.name, span._attrs);
  const isErr = statusCode(span.status) === "error";
  const left = total ? ((span.startNs - t0) / total) * 100 : 0;
  const width = total ? Math.max(0.6, (span.durationMs * 1e6 * 100) / total) : 0;
  const cost = span._attrs["hermes.cost.usage"];
  const tokens = span._attrs["gen_ai.usage.total_tokens"] || span._attrs["llm.token_count.total"];
  const approval = span._attrs["hermes.approval.choice"];
  return (
    <li className="border-b border-border last:border-b-0">
      <div className="flex cursor-pointer items-center gap-2 px-3 py-1.5 hover:bg-accent/30" style={{ paddingLeft: 12 + depth * 16 }} onClick={onToggle}>
        <span className="w-3 shrink-0 text-xs text-muted-foreground">{span.children.length ? (open ? "▾" : "▸") : ""}</span>
        <span className="inline-block h-2 w-2 shrink-0 rounded-full" style={{ background: KIND_HEX[kind] }} />
        <span className="truncate font-mono text-sm">{span.name}</span>
        {isErr ? <Badge variant="destructive" className="text-[10px]">error</Badge> : null}
        {approval ? <Badge variant="secondary" className="text-[10px]">👤 {approval}</Badge> : null}
        <div className="relative ml-2 hidden h-3 min-w-[80px] flex-1 bg-muted/30 sm:block">
          <div className="absolute top-0.5 h-2" style={{ left: `${left}%`, width: `${width}%`, background: KIND_HEX[kind], minWidth: 2 }} title={`${fmtDurationMs(span.durationMs)}`} />
        </div>
        {tokens ? <span className="hidden tabular-nums text-[11px] text-muted-foreground md:inline">{fmtTokens(tokens)} tok</span> : null}
        {cost ? <span className="hidden tabular-nums text-[11px] text-emerald-400 md:inline">${Number(cost).toFixed(4)}</span> : null}
        <span className="ml-auto shrink-0 tabular-nums text-xs text-muted-foreground">{fmtDurationMs(span.durationMs)}</span>
      </div>
      {open ? (
        <div className="bg-muted/20 px-4 py-3" style={{ paddingLeft: 28 + depth * 16 }}>
          <AttrTable attrs={span._attrs} />
        </div>
      ) : null}
    </li>
  );
}

// The reusable waterfall: pass tree roots; handles expand/collapse + timing bars.
export function SpanTreeView({ roots, defaultOpen }: { roots: TreeSpan[]; defaultOpen?: boolean }) {
  const flat = useMemo(() => flatten(roots), [roots]);
  const [openIds, setOpenIds] = useState<Record<string, boolean>>(() =>
    defaultOpen ? Object.fromEntries(flat.map((n) => [n.span.spanId, true])) : {}
  );
  if (!flat.length) return <div className="py-6 text-center text-sm text-muted-foreground">No spans.</div>;
  const t0 = Math.min(...flat.map((n) => n.span.startNs));
  const total = Math.max(...flat.map((n) => n.span.endNs)) - t0 || 1;
  const toggle = (id: string) => setOpenIds((p) => ({ ...p, [id]: !p[id] }));
  const expandAll = () => setOpenIds(Object.fromEntries(flat.map((n) => [n.span.spanId, true])));
  const collapseAll = () => setOpenIds({});
  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <span className="text-[11px] text-muted-foreground">
          {flat.length} span{flat.length === 1 ? "" : "s"} · {fmtDurationMs(total / 1e6)}
        </span>
        <div className="flex gap-2">
          <Button variant="outline" size="sm" onClick={expandAll}>Expand all</Button>
          <Button variant="outline" size="sm" onClick={collapseAll}>Collapse</Button>
        </div>
      </div>
      <ul className="overflow-hidden border border-border">
        {flat.map((n) => (
          <SpanRow key={n.span.spanId} span={n.span} depth={n.depth} open={!!openIds[n.span.spanId]} onToggle={() => toggle(n.span.spanId)} t0={t0} total={total} />
        ))}
      </ul>
    </div>
  );
}

// Compact trace card for the live store (root name, model/tokens/cost, meta).
export function LiveTraceCard({ trace, onSelect }: { trace: LiveTrace; onSelect: (t: LiveTrace) => void }) {
  const Icon = kindIcon(trace.rootKind);
  return (
    <div
      className={cn(
        "group flex cursor-pointer items-start gap-3 border bg-card/40 p-3 transition-colors hover:bg-secondary/30 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring",
        trace.error ? "border-destructive/30 bg-destructive/[0.04]" : "border-border"
      )}
      role="button"
      tabIndex={0}
      onClick={() => onSelect(trace)}
      onKeyDown={(e: any) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onSelect(trace);
        }
      }}
      title={trace.traceId}
    >
      <div className="shrink-0 pt-0.5" style={{ color: KIND_HEX[trace.rootKind] }}>
        <Icon size={16} />
      </div>
      <div className="min-w-0 flex-1 space-y-1">
        <div className="flex min-w-0 items-center gap-2">
          <span className="truncate font-mono text-sm">{trace.rootName}</span>
          {trace.error ? <Badge variant="destructive" className="shrink-0 text-[10px]">error</Badge> : null}
        </div>
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground">
          {trace.model ? <span className="font-mono text-foreground/80">{trace.model}</span> : null}
          <span className="tabular-nums">{trace.spanCount} span{trace.spanCount === 1 ? "" : "s"}</span>
          <span className="text-border">·</span>
          <span className="tabular-nums">{fmtDurationMs(trace.durationMs)}</span>
          {trace.tokens ? (
            <>
              <span className="text-border">·</span>
              <span className="tabular-nums">{fmtTokens(trace.tokens)} tok</span>
            </>
          ) : null}
          {trace.cost ? <span className="tabular-nums text-emerald-400">${trace.cost.toFixed(4)}</span> : null}
          <span className="text-border">·</span>
          <span title={fmtAbsTime(trace.startNs)}>{fmtTimeAgo(trace.endNs || trace.startNs)}</span>
        </div>
      </div>
      <div className="shrink-0 self-center text-muted-foreground opacity-30 transition-opacity group-hover:opacity-90">
        <IconChevronRight size={16} />
      </div>
    </div>
  );
}

// Full-screen-ish detail for a live trace: header + waterfall.
export function LiveTraceDetail({ trace, roots, onBack }: { trace: LiveTrace; roots: TreeSpan[]; onBack: () => void }) {
  return (
    <Card>
      <CardHeader className="flex flex-row items-start justify-between gap-3 space-y-0">
        <div className="min-w-0 space-y-1">
          <CardTitle className="truncate">{trace.rootName}</CardTitle>
          <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
            <span>{trace.service}</span>
            <span>·</span>
            <span className="font-mono">{String(trace.traceId).slice(0, 16)}</span>
            <span>·</span>
            <span>{fmtDurationMs(trace.durationMs)}</span>
            {trace.cost ? <span className="text-emerald-400">${trace.cost.toFixed(4)}</span> : null}
          </div>
        </div>
        <Button variant="ghost" size="sm" onClick={onBack}>← Back</Button>
      </CardHeader>
      <CardContent>
        <SpanTreeView roots={roots} defaultOpen />
      </CardContent>
    </Card>
  );
}
