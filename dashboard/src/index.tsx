import { React, useState, register, sdkOk } from "./sdk";
import { LivePage } from "./live";
import { TracesPage } from "./traces";

const TABS: { id: string; label: string; render: () => any }[] = [
  { id: "live", label: "⚡ Live", render: () => <LivePage /> },
  { id: "traces", label: "🌊 Traces", render: () => <TracesPage /> },
];

function OtelDashboard() {
  const [tab, setTab] = useState("live");
  const active = TABS.find((t) => t.id === tab) || TABS[0];
  return (
    <div className="hermes-otel-tab otel-root">
      <div className="otel-tabbar">
        {TABS.map((t) => (
          <button
            key={t.id}
            className={"otel-tabbtn" + (t.id === tab ? " otel-tabbtn-active" : "")}
            onClick={() => setTab(t.id)}
          >
            {t.label}
          </button>
        ))}
        <div className="otel-tabbar-spacer" />
        <span className="otel-brand">hermes-otel</span>
      </div>
      <div className="otel-tabbody">{active.render()}</div>
    </div>
  );
}

if (sdkOk) {
  register("hermes_otel", OtelDashboard);
} else {
  // eslint-disable-next-line no-console
  console.error("[hermes_otel] dashboard SDK not available — not registering");
}
