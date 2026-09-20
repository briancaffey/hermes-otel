// One selector for every tab: Live plus each configured backend (#177).
// Entries whose type has no adapter, or that lack the capability the tab
// needs, are shown but disabled with the reason in the title.
import { React, Select, SelectOption, cn } from "./sdk";
import { LIVE, SourceStatus } from "./source";

export type Need = "traces" | "metrics" | "logs";

export function backendUsable(b: { supported: boolean; metrics: boolean; logs: boolean }, need: Need): boolean {
  if (!b.supported) return false;
  if (need === "metrics") return b.metrics;
  if (need === "logs") return b.logs;
  return true;
}

export function SourceSelect({
  source,
  onChange,
  status,
  need,
  className,
}: {
  source: string;
  onChange: (s: string) => void;
  status: SourceStatus | null;
  need: Need;
  className?: string;
}) {
  const backends = status?.available || [];
  return (
    <div className={cn("inline-flex items-center gap-2", className)}>
      <span className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">source</span>
      <Select value={source} onValueChange={onChange} className="otel-w-56 h-8">
        <SelectOption value={LIVE}>⚡ Live (in-process)</SelectOption>
        {backends.map((b) => {
          const ok = backendUsable(b, need);
          const why = !b.supported
            ? "no dashboard adapter for this type"
            : need === "metrics" && !b.metrics
              ? "this backend does not serve metrics to the tab"
              : need === "logs" && !b.logs
                ? "this backend does not serve logs to the tab"
                : "";
          return (
            <SelectOption key={b.name} value={b.name} disabled={!ok} title={why}>
              🗄 {b.name}
              {b.type !== b.name ? ` (${b.type})` : ""}
              {ok ? "" : " · unavailable"}
            </SelectOption>
          );
        })}
      </Select>
    </div>
  );
}
