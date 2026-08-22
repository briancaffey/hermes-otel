// Shared atoms. Styling uses the HOST's Tailwind utility classes (so corners,
// colours and spacing match Hermes' theme exactly) — never custom rounding.
import { React, cn } from "./sdk";
import { Kind, KIND_HEX } from "./lib";

/* eslint-disable @typescript-eslint/no-explicit-any */

export function MiniLabel(props: { children: any }) {
  return <span className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">{props.children}</span>;
}

export function ErrorBanner({ error }: { error: string }) {
  return (
    <div className="border border-destructive/40 bg-destructive/10 px-3 py-2 text-xs text-destructive">
      {error}
    </div>
  );
}

export function KindDot({ kind, size = 8 }: { kind: Kind; size?: number }) {
  return (
    <span
      className="inline-block shrink-0 rounded-full"
      style={{ width: size, height: size, background: KIND_HEX[kind] }}
    />
  );
}

// A KPI tile. accent maps to host semantic colours.
export function Stat({
  label,
  value,
  sub,
  accent,
}: {
  label: string;
  value: any;
  sub?: any;
  accent?: "cost" | "error" | "ok";
}) {
  const valColor =
    accent === "cost" ? "text-emerald-400" : accent === "error" ? "text-destructive" : "text-foreground";
  return (
    <div className="border border-border bg-card/40 px-3 py-2.5">
      <div className={cn("text-xl font-semibold tabular-nums tracking-tight", valColor)}>{value}</div>
      <div className="text-[11px] uppercase tracking-wide text-muted-foreground">{label}</div>
      {sub != null ? <div className="mt-0.5 text-[11px] text-muted-foreground">{sub}</div> : null}
    </div>
  );
}

// Live pulse dot.
export function Pulse({ active }: { active: boolean }) {
  return (
    <span
      className={cn(
        "inline-block h-2.5 w-2.5 rounded-full",
        active ? "bg-emerald-400 otel-pulse" : "bg-muted-foreground/40"
      )}
    />
  );
}

// Tiny hand-rolled SVG bar sparkline (host ships no chart lib).
export function Sparkline({ values, height = 30, color }: { values: number[]; height?: number; color?: string }) {
  const max = Math.max(1, ...values);
  const w = values.length || 1;
  const bw = 100 / w;
  const fill = color || "var(--color-primary, #34d399)";
  return (
    <svg viewBox={`0 0 100 ${height}`} preserveAspectRatio="none" style={{ width: "100%", height }}>
      {values.map((v, i) => {
        const h = (v / max) * (height - 2);
        return (
          <rect
            key={i}
            x={i * bw + 0.25}
            y={height - h}
            width={Math.max(0.5, bw - 0.5)}
            height={h || 0.5}
            fill={fill}
            opacity={0.3 + 0.7 * (i / w)}
          />
        );
      })}
    </svg>
  );
}

// Simple multi-series line chart (SVG). series: {label,color,points:[v...]}.
export function LineChart({
  series,
  height = 120,
  labels,
}: {
  series: { label: string; color: string; points: number[] }[];
  height?: number;
  labels?: string[];
}) {
  const n = Math.max(1, ...series.map((s) => s.points.length));
  const max = Math.max(1, ...series.flatMap((s) => s.points));
  const W = 100;
  const path = (pts: number[]) =>
    pts
      .map((v, i) => `${i === 0 ? "M" : "L"} ${(i / Math.max(1, n - 1)) * W} ${height - (v / max) * (height - 6) - 3}`)
      .join(" ");
  return (
    <div>
      <svg viewBox={`0 0 ${W} ${height}`} preserveAspectRatio="none" style={{ width: "100%", height }}>
        {[0.25, 0.5, 0.75].map((g) => (
          <line key={g} x1={0} x2={W} y1={height * g} y2={height * g} stroke="var(--color-border)" strokeWidth={0.3} />
        ))}
        {series.map((s) => (
          <path key={s.label} d={path(s.points)} fill="none" stroke={s.color} strokeWidth={1} vectorEffect="non-scaling-stroke" />
        ))}
      </svg>
      <div className="mt-1 flex flex-wrap gap-3">
        {series.map((s) => (
          <span key={s.label} className="inline-flex items-center gap-1.5 text-[11px] text-muted-foreground">
            <span className="inline-block h-2 w-2 rounded-full" style={{ background: s.color }} />
            {s.label}
          </span>
        ))}
      </div>
    </div>
  );
}
