// Rendered smoke test per page: the host SDK is shimmed (test/sdk-shim.ts),
// fetchJSON answers from a small fixture per route, and each page must mount,
// settle, and show the text a person would look for. This catches what the
// pure-helper tests cannot: a component reading a field that is not there.
import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { act } from "react";
import { createRoot, Root } from "react-dom/client";
import React from "react";
import { fetchJSON, registered } from "./sdk-shim";

// Importing the entry registers the tab component with the shimmed registry.
import "../src/index";
import { LivePage } from "../src/live";
import { TracesPage } from "../src/traces";
import { MetricsPage } from "../src/metrics";
import { LogsPage } from "../src/logs";
import { SettingsPage } from "../src/settings";

(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;

const NOW = Date.now() * 1e6;
const span = (over: Record<string, any> = {}) => ({
  trace_id: "t1",
  span_id: "s1",
  parent_span_id: null,
  name: "agent",
  start_time_unix_nano: NOW - 2e9,
  end_time_unix_nano: NOW - 1e9,
  duration_ms: 1000,
  status: "OK",
  attributes: {
    "hermes.session_id": "sess-1",
    "gen_ai.request.model": "gpt-4o-mini",
    "gen_ai.usage.total_tokens": 42,
    "hermes.cost.usage": 0.0012,
    "input.value": "hello",
    "output.value": "world",
  },
  seq: 1,
  ...over,
});
const liveTrace = {
  traceId: "t1",
  rootName: "agent",
  rootKind: "agent",
  service: "hermes-agent",
  startNs: NOW - 2e9,
  endNs: NOW - 1e9,
  durationMs: 1000,
  spanCount: 1,
  model: "gpt-4o-mini",
  tokens: 42,
  cost: 0.0012,
  error: false,
  session: "sess-1",
};
const status = {
  configured: true,
  active: "phx",
  available: [{ name: "phx", type: "phoenix", supported: true, metrics: false, logs: false }],
  name: "phx",
  type: "phoenix",
};
const settingsReport = {
  resolved_at: Date.now() / 1000,
  reveal: false,
  config: {
    path: "/x/hermes_otel.yaml",
    path_source: "durable",
    exists: true,
    parse_ok: true,
    mtime: Date.now() / 1000,
    raw: "backends: []\n",
    raw_error: null,
    durable_path: "/x/hermes_otel.yaml",
    legacy_path: "/y",
    unknown_keys: [],
  },
  counts: { env: 0, file: 1, default: 3, changed: 1 },
  groups: ["General", "Backends"],
  fields: [
    {
      key: "dashboard_live",
      group: "General",
      kind: "bool",
      description: "Write to the live store",
      env_var: "HERMES_OTEL_DASHBOARD_LIVE",
      source: "default",
      default: true,
      file_value: null,
      file_invalid: false,
      env_raw: null,
      env_invalid: false,
      value: true,
      changed: false,
    },
    {
      key: "backends",
      group: "Backends",
      kind: "backends",
      description: "Backends",
      env_var: null,
      source: "file",
      default: [],
      file_value: [],
      file_invalid: false,
      env_raw: null,
      env_invalid: false,
      value: [],
      changed: true,
    },
  ],
  effective_yaml: "dashboard_live: true\n",
  env: [{ name: "HERMES_OTEL_DEBUG", group: "plugin", description: "debug", set: false, value: null, maps_to: null }],
  process: { role: "dashboard", pid: 1, python: "3.12", plugin_version: "1.19.0", hermes_home: "/x", note: "resolved by the dashboard process" },
  capture_summary: { mode: "preview", detail: "previews only", conversation_history: false, logs: false, sender_id: false },
};

function route(url: string): any {
  const path = url.split("?")[0].replace("/api/plugins/hermes_otel", "");
  switch (path) {
    case "/live/status":
      return { live: true, spans: 1, metrics: 0, logs: 0 };
    case "/live/spans":
      return { live: true, spans: [span()], cursor: 1 };
    case "/live/traces":
      return { live: true, traces: [liveTrace], total: 1 };
    case "/live/traces/t1":
      return { live: true, trace: liveTrace, spans: [span()] };
    case "/live/sessions":
      return { live: true, sessions: [] };
    case "/status":
      return status;
    case "/live/metrics/names":
      return { live: true, names: [{ name: "hermes.token.usage", count: 3 }] };
    case "/live/metrics/query":
      return {
        live: true,
        name: "hermes.token.usage",
        agg: "sum",
        bucketS: 60,
        buckets: [NOW - 60e9, NOW],
        series: { input: [10, 32], output: [5, 7] },
        points: 4,
      };
    case "/live/logs/search":
      return {
        live: true,
        logs: [
          {
            seq: 1,
            time_unix_nano: NOW - 1e9,
            level: "INFO",
            logger: "hermes",
            body: "turn started",
            trace_id: "t1",
            session_id: "sess-1",
            attributes: { "hermes.log.attribution": "context" },
          },
        ],
        next_before_ns: null,
        has_more: false,
      };
    case "/live/loggers":
      return { live: true, loggers: [{ logger: "hermes", count: 1 }] };
    case "/settings":
      return settingsReport;
    default:
      throw new Error(`404: unmocked ${url}`);
  }
}

let root: Root | null = null;
let host: HTMLDivElement;
const flush = async () => {
  await act(async () => {
    await new Promise((r) => setTimeout(r, 0));
  });
};
async function mount(Component: any) {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  await act(async () => {
    root!.render(React.createElement(Component));
  });
  await flush();
  await flush();
  return host;
}

beforeEach(() => {
  fetchJSON.mockReset();
  fetchJSON.mockImplementation(async (url: string) => route(url));
  window.history.replaceState(null, "", "/otel");
  localStorage.clear();
});
afterEach(async () => {
  if (root) await act(async () => root!.unmount());
  root = null;
  host?.remove();
});

describe("plugin registration", () => {
  it("registers the tab under the manifest name", () => {
    expect(typeof registered["hermes_otel"]).toBe("function");
  });
});

describe("pages mount against fixture payloads", () => {
  it("Live shows the KPI tiles and one turn card", async () => {
    const el = await mount(LivePage);
    expect(el.textContent).toContain("Live agent activity");
    expect(el.textContent).toContain("Turns");
    expect(el.textContent).toContain("agent");
    expect(el.textContent).toContain("gpt-4o-mini");
  });

  it("Traces (live source) lists the trace with its totals", async () => {
    const el = await mount(TracesPage);
    expect(el.textContent).toContain("1 of 1 trace");
    expect(el.textContent).toContain("gpt-4o-mini");
    expect(el.querySelector("form")).not.toBeNull();
  });

  it("Metrics shows the tiles and the explorer", async () => {
    const el = await mount(MetricsPage);
    expect(el.textContent).toContain("Tokens");
    expect(el.textContent).toContain("Explore any instrument");
    expect(el.textContent).toContain("hermes.token.usage");
  });

  it("Logs renders the line with its level, logger and trace link", async () => {
    const el = await mount(LogsPage);
    expect(el.textContent).toContain("turn started");
    expect(el.textContent).toContain("INFO");
    expect(el.textContent).toContain("following");
  });

  it("Settings renders the report header, a field row and the config line", async () => {
    const el = await mount(SettingsPage);
    expect(el.textContent).toContain("hermes-otel 1.19.0");
    expect(el.textContent).toContain("dashboard_live");
    expect(el.textContent).toContain("/x/hermes_otel.yaml");
  });

  it("a failing route shows the error banner instead of crashing", async () => {
    fetchJSON.mockImplementation(async (url: string) => {
      if (url.includes("/live/traces")) throw new Error('503: {"detail":"boom"}');
      return route(url);
    });
    const el = await mount(TracesPage);
    expect(el.textContent).toContain("boom");
  });
});
