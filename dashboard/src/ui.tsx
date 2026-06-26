import { React } from "./sdk";
import { Kind, KIND_COLOR } from "./lib";

export function Stat({
  label,
  value,
  accent,
}: {
  label: string;
  value: string;
  accent?: "cost" | "error";
}) {
  return (
    <div className={"otel-stat" + (accent ? " otel-stat-" + accent : "")}>
      <div className="otel-stat-value">{value}</div>
      <div className="otel-stat-label">{label}</div>
    </div>
  );
}

export function KindDot({ kind }: { kind: Kind }) {
  return <span className="otel-dot" style={{ background: KIND_COLOR[kind] }} />;
}

export function Pulse({ active }: { active: boolean }) {
  return <span className={"otel-pulse" + (active ? " otel-pulse-on" : "")} />;
}

// Tiny hand-rolled SVG bar sparkline (no chart lib in the host SDK).
export function Sparkline({ values, height = 28 }: { values: number[]; height?: number }) {
  const max = Math.max(1, ...values);
  const w = values.length;
  const bw = 100 / w;
  return (
    <svg className="otel-spark" viewBox={`0 0 100 ${height}`} preserveAspectRatio="none">
      {values.map((v, i) => {
        const h = (v / max) * (height - 2);
        return (
          <rect
            key={i}
            x={i * bw + 0.3}
            y={height - h}
            width={Math.max(0.6, bw - 0.6)}
            height={h}
            rx={0.4}
            fill="var(--otel-spark, var(--color-primary, #58a6ff))"
            opacity={0.35 + 0.65 * (i / w)}
          />
        );
      })}
    </svg>
  );
}

export function EmptyState({ title, hint }: { title: string; hint?: string }) {
  return (
    <div className="otel-empty">
      <div className="otel-empty-title">{title}</div>
      {hint ? <div className="otel-empty-hint">{hint}</div> : null}
    </div>
  );
}

export function ErrorBanner({ error }: { error: string }) {
  return <div className="otel-error">⚠ {error}</div>;
}
