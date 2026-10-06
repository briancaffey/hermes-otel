// The frontend's side of the milestone-8 contract: URL round trips for the
// Traces and Metrics tabs, the cursor stack, the one row shape, error reading.
import { describe, expect, it } from "vitest";
import {
  traceFiltersFromNav,
  navFromTraceFilters,
  DEFAULT_FILTERS,
  cursorsFromNav,
  navFromCursors,
  explorerFromNav,
  navFromExplorer,
  RANGES,
  fieldSupport,
  liveParams,
  backendParams,
} from "../src/params";
import { navSearch, readNav, clearOtherTabs } from "../src/nav";
import { rowFromBackend, rowFromLive, fmtCost, fmtInt } from "../src/lib";
import { describeError } from "../src/errors";
import { schemeOf } from "../src/index";
import { resolveInstrument } from "../src/metrics";
import { unusableReason } from "../src/sourceselect";
import { yTicks } from "../src/atoms";
import { axisTicks } from "../src/spantree";
import { activityBuckets, deriveStats } from "../src/live";

describe("trace filters in the URL (#281)", () => {
  it("round-trips every field and clears defaults", () => {
    const f = {
      ...DEFAULT_FILTERS,
      status: "error" as const,
      kind: "tool",
      tool: "terminal",
      model: "nano",
      session: "s1",
      minDurationMs: "250",
      text: "hi",
      q: "raw",
      service: "svc",
      rootsOnly: false,
      lookback: 24,
    };
    const search = navSearch({ tab: "traces", ...navFromTraceFilters(f), before: navFromCursors(["5", "3"]) }, "");
    const nav = readNav(search);
    expect(traceFiltersFromNav(nav)).toEqual({ ...f, traceId: "" });
    expect(cursorsFromNav(nav.before)).toEqual(["5", "3"]);
    expect(navFromTraceFilters(DEFAULT_FILTERS)).toEqual({
      status: "",
      kind: "",
      tool: "",
      model: "",
      session: "",
      mindur: "",
      text: "",
      q: "",
      service: "",
      roots: "",
      lookback: "",
    });
  });
  it("a session or trace link widens the default lookback", () => {
    expect(traceFiltersFromNav({ session: "s" }).lookback).toBe(168);
    expect(traceFiltersFromNav({}).lookback).toBe(1);
  });
  it("switching tabs clears the other tabs' keys but not the shared ones", () => {
    const patch = clearOtherTabs("logs");
    expect(patch.status).toBe("");
    expect(patch.range).toBe("");
    expect("trace" in patch).toBe(false);
    expect("lookback" in patch).toBe(false); // owned by logs too
  });
  it("sends the cursor and the kind prefix", () => {
    expect(liveParams(DEFAULT_FILTERS, 50, "123").get("before_ns")).toBe("123");
    expect(backendParams({ ...DEFAULT_FILTERS, kind: "llm" }, "phx", 50, "9").get("name_prefix")).toBe("llm.");
    expect(backendParams(DEFAULT_FILTERS, "phx", 50, "x").get("before_ns")).toBeNull();
  });
  it("reads a source's filter support", () => {
    expect(fieldSupport({ name: "server", free_text: "none" }, "kind")).toBe("server");
    expect(fieldSupport({ name: "server", free_text: "none" }, "text")).toBe("none");
    expect(fieldSupport(null, "text")).toBe("unknown");
    expect(fieldSupport({ status_ok: "client" }, "status")).toBe("client");
  });
});

describe("metrics explorer in the URL (#284)", () => {
  it("round-trips range, instrument, group and aggregation", () => {
    const s = { range: RANGES[3], instrument: "hermes.token.usage", groupBy: "model", agg: "avg" };
    const nav = readNav(navSearch({ tab: "metrics", ...navFromExplorer(s) }, ""));
    expect(explorerFromNav(nav)).toEqual(s);
    expect(navFromExplorer({ range: RANGES[1], instrument: "", groupBy: "", agg: "sum" })).toEqual({ range: "", inst: "", group: "", agg: "" });
  });
  it("resolves a canonical instrument against the source's catalogue", () => {
    const names = [
      { name: "hermes_token_usage" },
      { name: "hermes.cost.usage", otlp_name: "hermes.cost.usage" },
      { name: "custom_total", otlp_name: "hermes.model.usage" },
    ];
    expect(resolveInstrument(names, "hermes.token.usage")).toBe("hermes_token_usage");
    expect(resolveInstrument(names, "hermes.cost.usage")).toBe("hermes.cost.usage");
    expect(resolveInstrument(names, "hermes.model.usage")).toBe("custom_total");
    expect(resolveInstrument(names, "hw.gpu.utilization")).toBeNull();
    const hist = [{ name: "hermes_tool_duration_sum" }, { name: "hermes_tool_duration_count" }, { name: "hermes_tool_duration" }];
    expect(resolveInstrument(hist, "hermes.tool.duration", "_count")).toBe("hermes_tool_duration_count");
    expect(resolveInstrument(hist, "hermes.tool.duration")).toBe("hermes_tool_duration");
    const dotted = [{ name: "hermes.tool.duration.sum" }, { name: "hermes.tool.duration.count" }];
    expect(resolveInstrument(dotted, "hermes.tool.duration", "_count")).toBe("hermes.tool.duration.count");
    expect(resolveInstrument(dotted, "hermes.tool.duration", "_sum")).toBe("hermes.tool.duration.sum");
  });
  it("picks readable y ticks", () => {
    expect(yTicks(0)).toEqual([0]);
    expect(yTicks(7)).toEqual([0, 2.5, 5, 7.5]);
    expect(yTicks(1000)).toEqual([0, 500, 1000]);
  });
});

describe("one row shape for both sources (#281)", () => {
  it("normalises a backend card", () => {
    const t = {
      traceID: "abc",
      rootTraceName: "agent",
      rootServiceName: "hermes-agent",
      startTimeUnixNano: "1700000000000000000",
      durationMs: 1500,
      spanCount: 7,
      spanSets: [
        {
          spans: [
            {
              name: "agent",
              attributes: [
                { key: "gen_ai.request.model", value: { stringValue: "nano" } },
                { key: "gen_ai.usage.total_tokens", value: { intValue: "42" } },
                { key: "hermes.cost.usage", value: { doubleValue: 0.002 } },
                { key: "hermes.session_id", value: { stringValue: "s1" } },
                { key: "status", value: { stringValue: "error" } },
                { key: "input.value", value: { stringValue: "hello" } },
              ],
            },
          ],
        },
      ],
    };
    const r = rowFromBackend(t);
    expect(r).toMatchObject({
      traceId: "abc",
      rootName: "agent",
      rootKind: "agent",
      service: "hermes-agent",
      durationMs: 1500,
      spanCount: 7,
      model: "nano",
      tokens: 42,
      cost: 0.002,
      error: true,
      session: "s1",
      inPreview: "hello",
      partial: false,
    });
    expect(r.endNs - r.startNs).toBe(1500e6);
  });
  it("keeps a live row's flags", () => {
    const r = rowFromLive({
      traceId: "t",
      rootName: "api.x",
      rootKind: "api",
      service: "hermes",
      startNs: 1,
      endNs: 2,
      durationMs: 0.000001,
      spanCount: 1,
      model: null,
      tokens: null,
      cost: null,
      error: false,
      session: null,
      partial: true,
    });
    expect(r.partial).toBe(true);
    expect(r.cost).toBeNull();
  });
  it("never prints $0 for an unknown cost (#280)", () => {
    expect(fmtCost(null)).toBe("—");
    expect(fmtCost(0)).toBe("$0.00");
    expect(fmtCost(0.0042)).toBe("$0.0042");
    expect(fmtInt(null)).toBe("—");
  });
  it("derives the Live KPIs with unknowns kept unknown", () => {
    const rows = [
      rowFromLive({
        traceId: "a",
        rootName: "agent",
        rootKind: "agent",
        service: "",
        startNs: 1,
        endNs: 2,
        durationMs: 1,
        spanCount: 3,
        model: null,
        tokens: 10,
        cost: null,
        error: true,
        session: null,
      }),
    ];
    const s = deriveStats(rows);
    expect(s).toMatchObject({ cost: null, tokens: 10, errors: 1, turns: 1, spans: 3, byKind: { agent: 1 } });
    const now = 1_000_000;
    expect(activityBuckets([{ ...rows[0], endNs: (now - 1000) * 1e6, spanCount: 4 }], now).reduce((a, b) => a + b, 0)).toBe(4);
  });
});

describe("errors read the same way everywhere (#287)", () => {
  it("parses the host's status: body shape and the contract's kind", () => {
    const e = describeError(new Error('503: {"detail":"Backend \'phx\' has no dashboard adapter","kind":"config"}'));
    expect(e).toMatchObject({ status: 503, kind: "config", detail: "Backend 'phx' has no dashboard adapter" });
    expect(describeError(new Error("404: not found")).kind).toBe("not_found");
    expect(describeError(new Error("401: {}")).kind).toBe("auth");
    expect(describeError(new Error('422: {"detail":[{"msg":"bad"}]}')).detail).toBe("bad");
    expect(describeError(new Error("The dashboard server is unreachable")).kind).toBe("network");
    expect(describeError("boom").detail).toBe("boom");
  });
});

describe("shell helpers", () => {
  it("tells a light host theme from a dark one", () => {
    expect(schemeOf("rgb(255, 255, 255)")).toBe("light");
    expect(schemeOf("#0b0f14")).toBe("dark");
    expect(schemeOf("oklch(0.98 0 0)")).toBe("light");
    expect(schemeOf("hsl(220 10% 10%)")).toBe("dark");
  });
  it("explains why a backend cannot serve a tab", () => {
    expect(unusableReason({ supported: false, metrics: false, logs: false, type: "weave" }, "traces")).toMatch(/no dashboard adapter/);
    expect(unusableReason({ supported: true, metrics: false, logs: false, type: "phoenix" }, "metrics")).toMatch(/metrics/);
    expect(unusableReason({ supported: true, metrics: true, logs: true, type: "signoz" }, "logs")).toBeNull();
  });
  it("lays out the waterfall axis", () => {
    expect(axisTicks(0)).toEqual([]);
    expect(axisTicks(1000, 2).map((t) => t.label)).toEqual(["0µs", "500ms", "1.00s"]);
  });
});
