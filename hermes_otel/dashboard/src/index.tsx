import { React, useState, register, sdkOk, cn } from "./sdk";
import { LivePage } from "./live";
import { TracesPage } from "./traces";
import { MetricsPage } from "./metrics";
import { LogsPage } from "./logs";
import { IconActivity, IconList, IconChart } from "./icons";

/* eslint-disable @typescript-eslint/no-explicit-any */
const TABS = [
  { id: "live", label: "Live", Icon: IconActivity, render: () => <LivePage /> },
  { id: "traces", label: "Traces", Icon: IconList, render: () => <TracesPage /> },
  { id: "metrics", label: "Metrics", Icon: IconChart, render: () => <MetricsPage /> },
  { id: "logs", label: "Logs", Icon: IconList, render: () => <LogsPage /> },
];

function OtelDashboard() {
  const [tab, setTab] = useState("live");
  const active = TABS.find((t) => t.id === tab) || TABS[0];
  return (
    <div className="otel-root space-y-4">
      <div className="flex items-center gap-1 border-b border-border">
        {TABS.map((t) => {
          const on = t.id === tab;
          const Icon = t.Icon;
          return (
            <button
              key={t.id}
              onClick={() => setTab(t.id)}
              className={cn(
                "-mb-px inline-flex items-center gap-1.5 border-b-2 px-3 py-2 text-sm font-medium transition-colors",
                on
                  ? "border-foreground text-foreground"
                  : "border-transparent text-muted-foreground hover:text-foreground"
              )}
            >
              <Icon size={15} />
              {t.label}
            </button>
          );
        })}
        <span className="ml-auto pr-1 font-mono text-[11px] text-muted-foreground/60">hermes-otel</span>
      </div>
      <div>{active.render()}</div>
    </div>
  );
}

if (sdkOk) {
  register("hermes_otel", OtelDashboard);
} else {
  // eslint-disable-next-line no-console
  console.error("[hermes_otel] dashboard SDK unavailable — not registering");
}
