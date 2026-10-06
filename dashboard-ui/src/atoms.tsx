// Shared atoms. Styling uses the host's theme tokens (--color-*) through the
// plugin's own classes in dist/style.css (#180), never custom rounding.
import { React, useState, useRef, cn } from "./sdk";
import { ApiError, describeError } from "./errors";
import { IconAlert, IconCopy, IconCheck, IconSkipBack, IconArrowLeft, IconArrowRight } from "./icons";

export function MiniLabel(props: { children: any }) {
  return <span className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">{props.children}</span>;
}

/** A failed request, read the same way everywhere (#287). Pass the thrown error or a string. */
export function ErrorBanner({ error, prefix }: { error: unknown; prefix?: string }) {
  const e: ApiError = typeof error === "object" && error && "kind" in (error as any) && "detail" in (error as any) ? (error as ApiError) : describeError(error);
  return (
    <div role="alert" className="otel-error-banner">
      <IconAlert size={14} className="shrink-0" />
      <div className="min-w-0">
        <div>
          {prefix ? `${prefix}: ` : ""}
          {e.text}
          {e.status ? <span className="otel-error-status"> {e.status}</span> : null}
        </div>
        {e.detail && e.detail !== e.text ? <div className="otel-error-detail">{e.detail}</div> : null}
        {e.hint ? <div className="otel-error-hint">{e.hint}</div> : null}
      </div>
    </div>
  );
}

/** A div that behaves like a button for the keyboard too (#289). */
export function Clickable({
  onActivate,
  className,
  children,
  label,
  as = "div",
  ...rest
}: {
  onActivate: () => void;
  className?: string;
  children: any;
  label?: string;
  as?: string;
  [k: string]: any;
}) {
  const Tag = as as any;
  return (
    <Tag
      role="button"
      tabIndex={0}
      aria-label={label}
      className={className}
      onClick={onActivate}
      onKeyDown={(e: any) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onActivate();
        }
      }}
      {...rest}
    >
      {children}
    </Tag>
  );
}

/** Copies text; the label flips to "copied" for a moment. */
export function CopyButton({ text, label, className }: { text: string; label?: string; className?: string }) {
  const [done, setDone] = useState(false);
  return (
    <button
      type="button"
      className={cn("otel-link inline-flex items-center gap-1 text-[10px] text-muted-foreground", className)}
      title={`copy ${label || "to clipboard"}`}
      onClick={(e: any) => {
        e.stopPropagation();
        const p = navigator.clipboard?.writeText(text);
        if (p && typeof p.then === "function") p.catch(() => undefined);
        setDone(true);
        setTimeout(() => setDone(false), 1200);
      }}
    >
      {done ? <IconCheck size={11} /> : <IconCopy size={11} />}
      {done ? "copied" : label || "copy"}
    </button>
  );
}

// A KPI tile. `value` null/undefined reads as unknown (an em dash with `unknownText`
// under it), never as 0 (#280): a count that was not recorded is not zero.
export function Stat({
  label,
  value,
  sub,
  accent,
  unknownText,
}: {
  label: string;
  value: any;
  sub?: any;
  accent?: "cost" | "error" | "ok";
  unknownText?: string;
}) {
  const unknown = value == null;
  const valColor = unknown ? "text-muted-foreground" : accent === "cost" ? "otel-c-cost" : accent === "error" ? "text-destructive" : "text-foreground";
  return (
    <div className="otel-card-bg border border-border px-3 py-2.5">
      <div className={cn("text-xl font-semibold tabular-nums tracking-tight", valColor)}>{unknown ? "—" : value}</div>
      <div className="text-[11px] uppercase tracking-wide text-muted-foreground">{label}</div>
      {unknown && unknownText ? (
        <div className="mt-0.5 text-[11px] text-muted-foreground">{unknownText}</div>
      ) : sub != null ? (
        <div className="mt-0.5 text-[11px] text-muted-foreground">{sub}</div>
      ) : null}
    </div>
  );
}

// Live pulse dot.
export function Pulse({ active }: { active: boolean }) {
  return <span className={cn("inline-block h-2.5 w-2.5 rounded-full", active ? "otel-pulse-dot otel-pulse" : "bg-muted-foreground/40")} aria-hidden />;
}

// Tiny hand-rolled SVG bar sparkline (host ships no chart lib).
export function Sparkline({ values, height = 30, color, label }: { values: number[]; height?: number; color?: string; label?: string }) {
  const max = Math.max(1, ...values);
  const w = values.length || 1;
  const bw = 100 / w;
  const fill = color || "var(--otel-kind-agent)";
  return (
    <svg viewBox={`0 0 100 ${height}`} preserveAspectRatio="none" style={{ width: "100%", height }} role="img" aria-label={label || "activity over time"}>
      {values.map((v, i) => {
        const h = (v / max) * (height - 2);
        return <rect key={i} x={i * bw + 0.25} y={height - h} width={Math.max(0.5, bw - 0.5)} height={h || 0.5} fill={fill} opacity={0.3 + 0.7 * (i / w)} />;
      })}
    </svg>
  );
}

export type Series = { label: string; color: string; points: (number | null)[] };

/** Nice tick values for a 0..max axis. */
export function yTicks(max: number, n = 3): number[] {
  if (!(max > 0)) return [0];
  const raw = max / n;
  const pow = Math.pow(10, Math.floor(Math.log10(raw)));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * pow).find((s) => s >= raw) || raw;
  const out: number[] = [];
  for (let v = 0; ; v += step) {
    out.push(Number(v.toFixed(10)));
    if (v >= max - 1e-9) break;
  }
  return out;
}

// Multi-series line chart (SVG). A null point is a gap, not a zero (#284):
// the line breaks there. Hovering shows the nearest bucket's values; y ticks
// come from yTicks().
export function LineChart({
  series,
  height = 120,
  labels,
  fmt,
  bucketLabels,
}: {
  series: Series[];
  height?: number;
  /** three x labels: first, middle, last bucket */
  labels?: string[];
  fmt?: (n: number) => string;
  /** one label per bucket for the hover readout */
  bucketLabels?: string[];
}) {
  const [hover, setHover] = useState<number | null>(null);
  const box = useRef<any>(null);
  const n = Math.max(1, ...series.map((s) => s.points.length));
  const vals = series.flatMap((s) => s.points.filter((v): v is number => v != null));
  const rawMax = vals.length ? Math.max(...vals, 0) : 0;
  const ticks = yTicks(rawMax);
  const max = Math.max(ticks[ticks.length - 1] || 0, rawMax) || 1;
  const fmtY = (v: number) => (fmt ? fmt(v) : v >= 1000 ? `${(v / 1000).toFixed(v >= 10000 ? 0 : 1)}k` : v.toFixed(v < 10 && v !== Math.round(v) ? 2 : 0));
  const W = 100;
  const PAD = 3;
  const x = (i: number) => (i / Math.max(1, n - 1)) * W;
  const y = (v: number) => height - (v / max) * (height - 2 * PAD) - PAD;
  const path = (pts: (number | null)[]) => {
    let d = "";
    let pen = false;
    pts.forEach((v, i) => {
      if (v == null) {
        pen = false;
        return;
      }
      d += `${pen ? "L" : "M"} ${x(i).toFixed(2)} ${y(v).toFixed(2)} `;
      pen = true;
    });
    return d;
  };
  const onMove = (e: any) => {
    const r = box.current?.getBoundingClientRect?.();
    if (!r || !r.width) return;
    const i = Math.round(((e.clientX - r.left) / r.width) * (n - 1));
    setHover(Math.max(0, Math.min(n - 1, i)));
  };
  return (
    <div className="otel-chart">
      <div className="otel-chart-y" aria-hidden>
        {ticks
          .slice()
          .reverse()
          .map((t) => (
            <span key={t} style={{ top: `${((max - t) / max) * 100}%` }}>
              {fmtY(t)}
            </span>
          ))}
      </div>
      <div className="otel-chart-plot" ref={box} onMouseMove={onMove} onMouseLeave={() => setHover(null)}>
        <svg
          viewBox={`0 0 ${W} ${height}`}
          preserveAspectRatio="none"
          style={{ width: "100%", height }}
          role="img"
          aria-label={`${series.map((s) => s.label).join(", ")} over time`}
        >
          {ticks.map((t) => (
            <line key={t} x1={0} x2={W} y1={y(t)} y2={y(t)} stroke="var(--color-border)" strokeWidth={0.3} vectorEffect="non-scaling-stroke" />
          ))}
          {series.map((s) => (
            <path key={s.label} d={path(s.points)} fill="none" stroke={s.color} strokeWidth={1.2} vectorEffect="non-scaling-stroke" />
          ))}
          {hover != null ? (
            <line
              x1={x(hover)}
              x2={x(hover)}
              y1={0}
              y2={height}
              stroke="var(--color-foreground)"
              strokeWidth={0.6}
              opacity={0.5}
              vectorEffect="non-scaling-stroke"
            />
          ) : null}
          {hover != null
            ? series.map((s) =>
                s.points[hover] != null ? <circle key={s.label} cx={x(hover)} cy={y(s.points[hover] as number)} r={1.4} fill={s.color} /> : null
              )
            : null}
        </svg>
        {hover != null ? (
          <div className="otel-chart-tip" style={{ left: `${(hover / Math.max(1, n - 1)) * 100}%` }}>
            <div className="text-muted-foreground">{bucketLabels?.[hover] ?? `bucket ${hover + 1}`}</div>
            {series.map((s) => (
              <div key={s.label} className="flex items-center gap-1.5">
                <span className="otel-w-2 inline-block h-2 rounded-full" style={{ background: s.color }} />
                <span className="truncate">{s.label}</span>
                <span className="ml-auto tabular-nums">{s.points[hover] == null ? "no data" : fmtY(s.points[hover] as number)}</span>
              </div>
            ))}
          </div>
        ) : null}
      </div>
      <div className="otel-chart-x">{labels && labels.length ? labels.map((l, i) => <span key={i}>{l}</span>) : null}</div>
      <div className="mt-1 flex flex-wrap items-center gap-3">
        {series.map((s) => (
          <span key={s.label} className="inline-flex items-center gap-1.5 text-[11px] text-muted-foreground">
            <span className="otel-w-2 inline-block h-2 rounded-full" style={{ background: s.color }} />
            {s.label}
          </span>
        ))}
      </div>
    </div>
  );
}

/** Newest / Newer / Older for keyset-paged lists (#281, #285). */
export function Pager({
  page,
  hasMore,
  onNewest,
  onNewer,
  onOlder,
  range,
}: {
  page: number;
  hasMore: boolean;
  onNewest: () => void;
  onNewer: () => void;
  onOlder: () => void;
  range?: string;
}) {
  const first = page <= 1;
  return (
    <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground">
      <span>{range || ""}</span>
      <div className="flex items-center gap-2">
        <span className="tabular-nums">page {page}</span>
        <button type="button" className="otel-btn" onClick={onNewest} disabled={first} title="newest page">
          <IconSkipBack size={12} /> Newest
        </button>
        <button type="button" className="otel-btn" onClick={onNewer} disabled={first} title="newer page">
          <IconArrowLeft size={12} /> Newer
        </button>
        <button type="button" className="otel-btn" onClick={onOlder} disabled={!hasMore} title={hasMore ? "older page" : "no older rows"}>
          Older <IconArrowRight size={12} />
        </button>
      </div>
    </div>
  );
}

/** A button group acting as a segmented control (aria-pressed per option). */
export function Segmented<T extends string>({
  value,
  options,
  onChange,
  label,
}: {
  value: T;
  options: { id: T; label: string }[];
  onChange: (v: T) => void;
  label: string;
}) {
  return (
    <div className="otel-card-bg inline-flex border border-border p-0.5" role="group" aria-label={label}>
      {options.map((o) => (
        <button
          key={o.id}
          type="button"
          aria-pressed={value === o.id}
          onClick={() => onChange(o.id)}
          className={cn(
            "otel-toggle px-3 py-1 text-xs font-medium transition-colors",
            value === o.id ? "otel-toggle-active text-foreground" : "text-muted-foreground hover:text-foreground"
          )}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

/** Dashed empty state with a title and a hint. */
export function Empty({ title, children, className }: { title: string; children?: any; className?: string }) {
  return (
    <div className={cn("border border-dashed border-border px-4 py-10 text-center text-sm text-muted-foreground", className)}>
      <div className="mb-1 text-base font-medium text-foreground">{title}</div>
      {children}
    </div>
  );
}

/** A labelled switch (the host's Checkbox is a role=switch button). */
export function Toggle({
  checked,
  onChange,
  label,
  title,
  Switch,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  label: any;
  title?: string;
  Switch?: any;
}) {
  const id = useRef(`otel-sw-${Math.random().toString(36).slice(2, 8)}`);
  return (
    <span className="inline-flex items-center gap-1.5 text-[11px] text-muted-foreground" title={title}>
      {Switch ? (
        <Switch checked={checked} onCheckedChange={onChange} id={id.current} />
      ) : (
        <input id={id.current} type="checkbox" checked={checked} onChange={(e: any) => onChange(e.target.checked)} />
      )}
      <label htmlFor={id.current} className="cursor-pointer">
        {label}
      </label>
    </span>
  );
}
