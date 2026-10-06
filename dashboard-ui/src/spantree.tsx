// Shared span-tree waterfall + trace cards. Used by both the Traces browser and
// the Live "recent turns" feed so live spans and backend spans render identically.
//
// The waterfall is one full-width card per span, each topped by a strip that
// is positioned by (start−t0)/total and sized by dur/total against the trace's
// whole duration, so the strips line up into a flamegraph; nesting is shown by
// indenting the card's content. A time axis above the list gives the scale,
// and every row header is keyboard-operable (#283, #289).
import { React, useState, useMemo, useEffect, Card, CardHeader, CardContent, Badge, Button, cn } from "./sdk";
import {
  fmtDurationMs,
  fmtTokens,
  fmtCostExact,
  fmtTimeAgo,
  fmtAbsTime,
  kindOf,
  statusCode,
  KIND_COLOR,
  TreeSpan,
  flatten,
  LiveTrace,
  TraceRow,
  rowFromLive,
  findRoot,
} from "./lib";
import { kindIcon, IconChevronRight } from "./icons";
import { TraceHeader, TraceTabs, SpanSummary, AttrGroups } from "./detail";
import { Clickable } from "./atoms";

// One span per line = a full-width collapsible card. The coloured LINE along the
// top is the flamegraph bar: positioned by offset and sized by duration, all
// against the SAME full-card scale (every card is full width) so the strips line
// up into a flamegraph. Nesting is shown by indenting the card's content only.
function SpanSection({
  span,
  depth,
  open,
  onToggle,
  startMs,
  offsetPct,
  durPct,
  hasKids,
  source,
}: {
  span: TreeSpan;
  depth: number;
  open: boolean;
  onToggle: () => void;
  startMs: number;
  offsetPct: number;
  durPct: number;
  hasKids: boolean;
  source?: string;
}) {
  const kind = kindOf(span.name, span._attrs);
  const hex = KIND_COLOR[kind];
  const isErr = statusCode(span.status) === "error";
  const cost = span._attrs["hermes.cost.usage"];
  const tokens = span._attrs["gen_ai.usage.total_tokens"] || span._attrs["llm.token_count.total"];
  const approval = span._attrs["hermes.approval.choice"];
  const width = Math.min(100 - offsetPct, Math.max(0.8, durPct));
  return (
    <div className={cn("otel-card-bg otel-hoverable overflow-hidden border transition-colors", isErr ? "border-destructive/40" : "border-border")}>
      {/* flamegraph line: full-width track + a coloured segment (offset → duration) */}
      <div className="otel-track relative h-1.5 w-full" title={`+${fmtDurationMs(startMs)} · ${fmtDurationMs(span.durationMs)}`}>
        <div className="absolute inset-y-0" style={{ left: `${offsetPct}%`, width: `${width}%`, minWidth: 2, background: hex }} />
      </div>
      <Clickable
        onActivate={onToggle}
        label={`${open ? "collapse" : "expand"} span ${span.name}`}
        aria-expanded={open}
        className="otel-row flex items-center gap-2 px-3 py-2"
        style={{ paddingLeft: 12 + depth * 20 }}
      >
        <span className="w-3 shrink-0 text-xs text-muted-foreground" aria-hidden>
          {open ? "▾" : "▸"}
        </span>
        <span className="otel-w-2 inline-block h-2 shrink-0 rounded-full" style={{ background: hex }} aria-hidden />
        <span className="truncate font-mono text-sm" title={span.name}>
          {span.name}
        </span>
        {hasKids ? <span className="text-[10px] text-muted-foreground">{span.children.length}</span> : null}
        {isErr ? (
          <Badge variant="destructive" className="shrink-0 text-[10px]">
            error
          </Badge>
        ) : null}
        {approval ? (
          <Badge variant="secondary" className="shrink-0 text-[10px]">
            approval: {approval}
          </Badge>
        ) : null}
        <div className="ml-auto flex shrink-0 items-center gap-3 text-[11px] text-muted-foreground">
          {tokens ? <span className="tabular-nums">{fmtTokens(tokens)} tok</span> : null}
          {cost != null ? <span className="tabular-nums otel-c-cost">{fmtCostExact(Number(cost))}</span> : null}
          {startMs > 0.5 ? (
            <span className="tabular-nums" title="start offset from trace begin">
              +{fmtDurationMs(startMs)}
            </span>
          ) : null}
          <span className="otel-w-14 text-right font-medium tabular-nums text-foreground">{fmtDurationMs(span.durationMs)}</span>
        </div>
      </Clickable>
      {open ? (
        <div className="space-y-3 border-t border-border/60 bg-muted/20 px-3 py-3">
          <SpanSummary span={span} source={source} />
          <details className="otel-details">
            <summary className="cursor-pointer text-[11px] font-medium uppercase tracking-wide text-muted-foreground">all attributes</summary>
            <div className="mt-2">
              <AttrGroups attrs={span._attrs} source={source} />
            </div>
          </details>
        </div>
      ) : null}
    </div>
  );
}

/** Tick marks for the waterfall's shared time scale. */
export function axisTicks(totalMs: number, n = 5): { pct: number; label: string }[] {
  if (!(totalMs > 0)) return [];
  const out: { pct: number; label: string }[] = [];
  for (let i = 0; i <= n; i++) out.push({ pct: (i / n) * 100, label: fmtDurationMs((totalMs * i) / n) });
  return out;
}

// Reusable span tree: collapsible cards, each topped by its kind colour.
// Collapsed by default for both sources (#283); "Expand all" opens every row.
export function SpanTreeView({ roots, defaultOpen, source }: { roots: TreeSpan[]; defaultOpen?: boolean; source?: string }) {
  const flat = useMemo(() => flatten(roots), [roots]);
  const [openIds, setOpenIds] = useState<Record<string, boolean>>(() => (defaultOpen ? Object.fromEntries(flat.map((n) => [n.span.spanId, true])) : {}));
  // Spans that arrive later (a live trace still running) follow the current choice.
  const [allOpen, setAllOpen] = useState(!!defaultOpen);
  useEffect(() => {
    if (allOpen) setOpenIds((p) => ({ ...Object.fromEntries(flat.map((n) => [n.span.spanId, true])), ...p }));
  }, [flat, allOpen]);
  if (!flat.length) return <div className="py-6 text-center text-sm text-muted-foreground">No spans.</div>;
  const t0 = Math.min(...flat.map((n) => n.span.startNs));
  const total = Math.max(...flat.map((n) => n.span.endNs)) - t0 || 1;
  const toggle = (id: string) => setOpenIds((p) => ({ ...p, [id]: !p[id] }));
  const expandAll = () => {
    setAllOpen(true);
    setOpenIds(Object.fromEntries(flat.map((n) => [n.span.spanId, true])));
  };
  const collapseAll = () => {
    setAllOpen(false);
    setOpenIds({});
  };
  const ticks = axisTicks(total / 1e6);
  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <span className="text-[11px] text-muted-foreground">
          {flat.length} span{flat.length === 1 ? "" : "s"} · {fmtDurationMs(total / 1e6)} total · {roots.length > 1 ? `${roots.length} roots` : "1 root"}
        </span>
        <div className="flex gap-2">
          <Button variant="outline" size="sm" onClick={expandAll}>
            Expand all
          </Button>
          <Button variant="outline" size="sm" onClick={collapseAll}>
            Collapse
          </Button>
        </div>
      </div>
      <div className="otel-axis" aria-hidden>
        {ticks.map((t) => (
          <React.Fragment key={t.pct}>
            <i style={{ left: `${t.pct}%` }} />
            <span style={{ left: `${t.pct}%` }}>{t.label}</span>
          </React.Fragment>
        ))}
      </div>
      <div className="flex flex-col gap-1.5">
        {flat.map((n) => (
          <SpanSection
            key={n.span.spanId}
            span={n.span}
            depth={n.depth}
            open={!!openIds[n.span.spanId]}
            onToggle={() => toggle(n.span.spanId)}
            startMs={(n.span.startNs - t0) / 1e6}
            offsetPct={((n.span.startNs - t0) / total) * 100}
            durPct={(n.span.durationMs * 1e6 * 100) / total}
            hasKids={n.span.children.length > 0}
            source={source}
          />
        ))}
      </div>
    </div>
  );
}

// One trace card for every source (#281): the normalised TraceRow carries
// what a card shows; previews and the tool badge appear when the source has them.
export function TraceCard({ row, onSelect }: { row: TraceRow; onSelect: (r: TraceRow) => void }) {
  const Icon = kindIcon(row.rootKind);
  return (
    <Clickable
      onActivate={() => onSelect(row)}
      label={`open trace ${row.rootName}`}
      className={cn(
        "otel-card-bg otel-hover-parent flex cursor-pointer items-start gap-3 border p-3 transition-colors hover:bg-secondary/30",
        row.error ? "otel-error-bg border-destructive/30" : "border-border"
      )}
      title={row.traceId}
    >
      <div className="shrink-0 pt-0.5" style={{ color: KIND_COLOR[row.rootKind] }} aria-hidden>
        <Icon size={16} />
      </div>
      <div className="min-w-0 flex-1 space-y-1">
        <div className="flex min-w-0 items-center gap-2">
          <span className="truncate font-mono text-sm">{row.rootName}</span>
          {row.rootKind !== "other" ? (
            <Badge variant="secondary" className="shrink-0 text-[10px]">
              {row.rootKind}
            </Badge>
          ) : null}
          {row.error ? (
            <Badge variant="destructive" className="shrink-0 text-[10px]">
              error
            </Badge>
          ) : null}
          {row.partial ? (
            <Badge variant="secondary" className="shrink-0 text-[10px]" title="the root span has not finished: name, kind and totals are provisional">
              in progress
            </Badge>
          ) : null}
          {row.toolName ? (
            <Badge variant="secondary" className="shrink-0 font-mono text-[10px]">
              {row.toolName}
            </Badge>
          ) : null}
        </div>
        {row.inPreview || row.outPreview ? (
          <div className="otel-pl-2 space-y-0.5 border-l-2 border-border/60 text-xs">
            {row.inPreview ? (
              <div className="truncate text-foreground/80">
                <span className="otel-mr-2 text-[10px] text-muted-foreground">in</span>
                {row.inPreview}
              </div>
            ) : null}
            {row.outPreview ? (
              <div className="truncate text-foreground/80">
                <span className="otel-mr-2 text-[10px] text-muted-foreground">out</span>
                {row.outPreview}
              </div>
            ) : null}
          </div>
        ) : null}
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground">
          {row.model ? <span className="font-mono text-foreground/80">{row.model}</span> : null}
          {row.service && row.service !== "hermes" ? <span>{row.service}</span> : null}
          <span className="tabular-nums">{row.spanCount != null ? `${row.spanCount} span${row.spanCount === 1 ? "" : "s"}` : "spans ?"}</span>
          <span className="text-border" aria-hidden>
            ·
          </span>
          <span className="tabular-nums">{fmtDurationMs(row.durationMs)}</span>
          {row.tokens != null ? (
            <>
              <span className="text-border" aria-hidden>
                ·
              </span>
              <span className="tabular-nums">{fmtTokens(row.tokens)} tok</span>
            </>
          ) : null}
          {row.cost != null ? <span className="tabular-nums otel-c-cost">{fmtCostExact(row.cost)}</span> : null}
          <span className="text-border" aria-hidden>
            ·
          </span>
          <span title={fmtAbsTime(row.startNs)}>{fmtTimeAgo(row.endNs || row.startNs)}</span>
        </div>
      </div>
      <div className="otel-self-center otel-reveal shrink-0 text-muted-foreground transition-opacity" aria-hidden>
        <IconChevronRight size={16} />
      </div>
    </Clickable>
  );
}

/** Backwards-compatible card for a live row. */
export function LiveTraceCard({ trace, onSelect }: { trace: LiveTrace; onSelect: (t: LiveTrace) => void }) {
  return <TraceCard row={rowFromLive(trace)} onSelect={() => onSelect(trace)} />;
}

// Full detail for a live trace: summary header, then Spans / Logs / Raw.
export function LiveTraceDetail({
  trace,
  roots,
  onBack,
  source = "live",
  loading,
}: {
  trace: LiveTrace;
  roots: TreeSpan[];
  onBack: () => void;
  source?: string;
  loading?: boolean;
}) {
  const spans = trace.spans || [];
  const root = findRoot(spans) || spans[0];
  const startNs = spans.length ? Math.min(...spans.map((s) => s.start_time_unix_nano || 0)) : trace.startNs;
  const endNs = spans.length ? Math.max(...spans.map((s) => s.end_time_unix_nano || s.start_time_unix_nano || 0)) : trace.endNs;
  return (
    <Card>
      <CardHeader className="otel-space-y-0">
        <TraceHeader
          title={trace.rootName}
          traceId={String(trace.traceId)}
          service={trace.service}
          durationMs={trace.durationMs}
          rootAttrs={root?.attributes || {}}
          spans={spans.map((s) => ({ name: s.name, attributes: s.attributes || {} }))}
          error={trace.error}
          partial={!!trace.partial || (spans.length > 0 && !findRoot(spans))}
          source={source}
          onBack={onBack}
        />
      </CardHeader>
      <CardContent>
        {loading ? <div className="text-xs text-muted-foreground">Loading spans…</div> : null}
        <TraceTabs
          traceId={String(trace.traceId)}
          source={source}
          logsAvailable
          spans={<SpanTreeView roots={roots} source={source} />}
          raw={spans}
          windowNs={[startNs, endNs]}
        />
      </CardContent>
    </Card>
  );
}
