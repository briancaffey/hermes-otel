// Unit tests for the pure helpers in src/lib.ts (#189). The fixture is a real
// turn captured from the live store on 2026-09-20: three API calls
// (13989 + 14268 + 14381 = 42,638 tokens), two tool calls, one llm span and
// the agent root that carries the turn total.
import { describe, expect, it } from "vitest";
import { groupLiveTraces, traceTotals, traceSpanCount, LiveSpan, metricOtlpName } from "../src/lib";

const T = "daaf7828a1b2c3d4e5f60718293a4b5c";
function span(name: string, id: string, parent: string | null, attrs: Record<string, any> = {}, start = 0, end = 1): LiveSpan {
  return {
    trace_id: T, span_id: id, parent_span_id: parent, name,
    start_time_unix_nano: 1_000_000 + start, end_time_unix_nano: 1_000_000 + end,
    duration_ms: (end - start) / 1e6, status: "OK", attributes: attrs, seq: 0,
  };
}
const MODEL = "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning-nvfp4";
const turn: LiveSpan[] = [
  span(`api.${MODEL}`, "a1", "l1", { "gen_ai.usage.total_tokens": 13989, "llm.token_count.total": 13989, "gen_ai.request.model": MODEL }),
  span("tool.write_file", "t1", "l1"),
  span(`api.${MODEL}`, "a2", "l1", { "gen_ai.usage.total_tokens": 14268, "llm.token_count.total": 14268 }),
  span("tool.read_file", "t2", "l1"),
  span(`api.${MODEL}`, "a3", "l1", { "gen_ai.usage.total_tokens": 14381, "llm.token_count.total": 14381 }),
  span(`llm.${MODEL}`, "l1", "root", { "gen_ai.request.model": MODEL }),
  span("agent", "root", null, { "gen_ai.usage.total_tokens": 42638, "llm.token_count.total": 42638, "gen_ai.request.model": MODEL }, 0, 64_240_000_000),
];

describe("traceTotals (#178)", () => {
  it("counts a turn once: the root's total, not root + api spans", () => {
    expect(traceTotals(turn)).toEqual({ tokens: 42638, cost: null });
  });
  it("sums api.* spans only when the root carries no total", () => {
    const noRootTotal = turn.map((s) => (s.name === "agent" ? { ...s, attributes: {} } : s));
    expect(traceTotals(noRootTotal).tokens).toBe(42638);
  });
  it("never counts llm.* spans, which mirror the api spans", () => {
    const withLlmTotals = turn.map((s) =>
      s.name.startsWith("llm.") ? { ...s, attributes: { ...s.attributes, "gen_ai.usage.total_tokens": 42638 } } : s.name === "agent" ? { ...s, attributes: {} } : s
    );
    expect(traceTotals(withLlmTotals).tokens).toBe(42638);
  });
  it("reports no cost rather than $0 when nothing carries a cost", () => {
    expect(traceTotals(turn).cost).toBeNull();
  });
  it("takes cost from the root when present, else the api spans", () => {
    const withCost = turn.map((s) => (s.name.startsWith("api.") ? { ...s, attributes: { ...s.attributes, "hermes.cost.usage": 0.01 } } : s));
    expect(traceTotals(withCost).cost).toBeCloseTo(0.03);
  });
});

describe("groupLiveTraces", () => {
  it("builds one trace with the corrected totals and the span count", () => {
    const [t] = groupLiveTraces(turn);
    expect(t.traceId).toBe(T);
    expect(t.rootName).toBe("agent");
    expect(t.spanCount).toBe(7);
    expect(t.tokens).toBe(42638);
    expect(t.cost).toBeNull();
    expect(t.model).toBe(MODEL);
  });
});

describe("traceSpanCount (#179)", () => {
  it("uses the adapter's spanCount", () => {
    expect(traceSpanCount({ spanCount: 5, spanSets: [{ spans: [{}] }] })).toBe(5);
  });
  it("uses Tempo's per-service stats", () => {
    expect(traceSpanCount({ serviceStats: { a: { spanCount: 2 }, b: { spanCount: 3 } } })).toBe(5);
  });
  it("shows nothing rather than the matched-span count", () => {
    expect(traceSpanCount({ spanSets: [{ spans: [{}], matched: 1 }] })).toBeNull();
  });
});

describe("metricOtlpName", () => {
  it("maps Prometheus-style names back to the OTLP name", () => {
    expect(metricOtlpName("hermes_token_usage")).toBe("hermes.token.usage");
    expect(metricOtlpName("hermes_tool_duration_sum")).toBe("hermes.tool.duration");
    expect(metricOtlpName("hermes_prompt_cache_tokens")).toBe("hermes.prompt_cache.tokens");
    expect(metricOtlpName("hermes.session.count")).toBe("hermes.session.count");
    expect(metricOtlpName("process.cpu.utilization")).toBe("process.cpu.utilization");
  });
});
