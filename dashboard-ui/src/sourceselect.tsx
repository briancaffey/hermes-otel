// One selector for every tab: Live plus each configured backend that can
// serve what the tab shows (#177). The host's Select ignores `disabled` and
// `title` on an option and reads only a plain-string label (sdk.ts), so an
// entry the tab cannot use is not an option; it is listed next to the
// selector with the reason instead.
import { React, Select, SelectOption, cn } from "./sdk";
import { LIVE, SourceStatus, Need, backendUsable } from "./source";
import { IconZap, IconDatabase } from "./icons";

export function unusableReason(b: { supported: boolean; metrics: boolean; logs: boolean; type: string }, need: Need): string | null {
  if (!b.supported) return `no dashboard adapter for type ${b.type}`;
  if (need === "metrics" && !b.metrics) return "does not serve metrics to the tab";
  if (need === "logs" && !b.logs) return "does not serve logs to the tab";
  return null;
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
  const usable = backends.filter((b) => backendUsable(b, need));
  const unusable = backends.filter((b) => !backendUsable(b, need));
  const isLive = source === LIVE;
  return (
    <div className={cn("inline-flex flex-wrap items-center gap-2", className)}>
      <label className="inline-flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-wide text-muted-foreground" htmlFor="otel-source">
        {isLive ? <IconZap size={12} className="otel-c-agent" /> : <IconDatabase size={12} />}
        source
      </label>
      <Select id="otel-source" value={source} onValueChange={onChange} className="otel-w-56 h-8">
        <SelectOption value={LIVE}>Live (in-process)</SelectOption>
        {usable.map((b) => (
          <SelectOption key={b.name} value={b.name}>
            {b.type !== b.name ? `${b.name} (${b.type})` : b.name}
          </SelectOption>
        ))}
      </Select>
      {unusable.length ? (
        <span className="text-[11px] text-muted-foreground" title={unusable.map((b) => `${b.name}: ${unusableReason(b, need)}`).join("\n")}>
          {unusable.length} backend{unusable.length === 1 ? "" : "s"} cannot serve this tab
        </span>
      ) : null}
    </div>
  );
}
