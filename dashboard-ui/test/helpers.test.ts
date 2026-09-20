// The rest of src/lib.ts: OTLP decoding, tree building, previews, status, kinds (#189).
import { describe, expect, it } from "vitest";
import {
  attrsObject,
  buildSpanTree,
  clip,
  decodeAttrValue,
  extractInputPreview,
  extractOutputPreview,
  flatten,
  fmtDurationMs,
  fmtTokens,
  isMcpKeepalivePing,
  kindOf,
  liveTreeFromSpans,
  statusCode,
  traceAttrs,
} from "../src/lib";

describe("OTLP attribute decoding", () => {
  it("decodes every value type the adapters emit", () => {
    expect(decodeAttrValue({ stringValue: "x" })).toBe("x");
    expect(decodeAttrValue({ intValue: "42" })).toBe(42);
    expect(decodeAttrValue({ doubleValue: 1.5 })).toBe(1.5);
    expect(decodeAttrValue({ boolValue: true })).toBe(true);
    expect(decodeAttrValue({ arrayValue: { values: [{ intValue: "1" }, { stringValue: "b" }] } })).toEqual([1, "b"]);
    expect(decodeAttrValue({ kvlistValue: { values: [{ key: "k", value: { stringValue: "v" } }] } })).toEqual({ k: "v" });
    expect(decodeAttrValue(null)).toBeNull();
    expect(decodeAttrValue("plain")).toBe("plain");
  });
  it("builds a flat object and skips entries without a key", () => {
    expect(attrsObject([{ key: "a", value: { intValue: "1" } }, { value: { intValue: "2" } } as any])).toEqual({ a: 1 });
  });
});

describe("buildSpanTree", () => {
  const batches = [
    {
      scopeSpans: [
        {
          spans: [
            { spanId: "c", parentSpanId: "r", name: "tool.x", startTimeUnixNano: "20", endTimeUnixNano: "30", attributes: [] },
            { spanId: "r", name: "agent", startTimeUnixNano: "10", endTimeUnixNano: "40", status: { code: 2 }, attributes: [{ key: "k", value: { stringValue: "v" } }] },
            { spanId: "orphan", parentSpanId: "missing", name: "api.y", startTimeUnixNano: "5", endTimeUnixNano: "6", attributes: [] },
          ],
        },
      ],
    },
  ];
  it("nests children, keeps orphans as roots, sorts by start", () => {
    const { roots, all } = buildSpanTree(batches);
    expect(all).toHaveLength(3);
    expect(roots.map((r) => r.name)).toEqual(["api.y", "agent"]);
    const agent = roots[1];
    expect(agent.children.map((c) => c.name)).toEqual(["tool.x"]);
    expect(agent.durationMs).toBeCloseTo(30 / 1e6);
    expect(agent._attrs).toEqual({ k: "v" });
    expect(flatten(roots).map((n) => [n.span.name, n.depth])).toEqual([["api.y", 0], ["agent", 0], ["tool.x", 1]]);
  });
  it("accepts snake_case OTLP too", () => {
    const { all } = buildSpanTree([{ scope_spans: [{ spans: [{ span_id: "a", name: "n", start_time_unix_nano: "1", end_time_unix_nano: "2" }] }] }]);
    expect(all[0].spanId).toBe("a");
  });
  it("returns nothing for empty input", () => {
    expect(buildSpanTree([])).toEqual({ roots: [], all: [] });
  });
});

describe("liveTreeFromSpans", () => {
  it("maps live spans to the same tree shape, ERROR becoming status code 2", () => {
    const { roots } = liveTreeFromSpans([
      { trace_id: "t", span_id: "r", parent_span_id: null, name: "agent", start_time_unix_nano: 1, end_time_unix_nano: 5, duration_ms: 0.000004, status: "OK", attributes: {}, seq: 1 },
      { trace_id: "t", span_id: "c", parent_span_id: "r", name: "tool.t", start_time_unix_nano: 2, end_time_unix_nano: 3, duration_ms: 0.000001, status: "ERROR", attributes: {}, seq: 2 },
    ]);
    expect(roots).toHaveLength(1);
    expect(statusCode(roots[0].children[0].status)).toBe("error");
    expect(statusCode(roots[0].status)).toBeNull();
  });
});

describe("previews", () => {
  it("takes the last user message out of a messages array", () => {
    const msgs = JSON.stringify([{ role: "system", content: "s" }, { role: "user", content: "first" }, { role: "assistant", content: "a" }, { role: "user", content: [{ type: "text", text: "last" }] }]);
    expect(extractInputPreview({ "input.value": msgs })).toBe("last");
  });
  it("falls back to the raw string and to the first content", () => {
    expect(extractInputPreview({ "input.value": "plain prompt" })).toBe("plain prompt");
    expect(extractInputPreview({ "input.value": JSON.stringify([{ role: "assistant", content: "only" }]) })).toBe("only");
    expect(extractInputPreview({ "input.value": "[not json" })).toBe("[not json");
    expect(extractInputPreview({})).toBeNull();
  });
  it("prefers llm.output.content for the output", () => {
    expect(extractOutputPreview({ "llm.output.content": "a", "output.value": "b" })).toBe("a");
    expect(extractOutputPreview({ "output.value": "b" })).toBe("b");
  });
  it("clips and collapses whitespace", () => {
    expect(clip("a   b\n c", 100)).toBe("a b c");
    expect(clip("abcdefgh", 5)).toBe("abcd…");
    expect(clip("   ", 5)).toBeNull();
  });
});

describe("statusCode / kindOf / formatting", () => {
  it("recognises every status spelling", () => {
    expect(statusCode({ code: 2 })).toBe("error");
    expect(statusCode({ code: "STATUS_CODE_ERROR" })).toBe("error");
    expect(statusCode({ statusCode: 1 })).toBe("ok");
    expect(statusCode("OK")).toBe("ok");
    expect(statusCode({ code: 0 })).toBeNull();
    expect(statusCode(null)).toBeNull();
  });
  it("classifies span names and hermes.span_kind overrides", () => {
    expect(kindOf("api.gpt-4")).toBe("api");
    expect(kindOf("llm.x")).toBe("llm");
    expect(kindOf("tool.terminal")).toBe("tool");
    expect(kindOf("agent")).toBe("agent");
    expect(kindOf("cron:daily")).toBe("cron");
    expect(kindOf("subagent.x")).toBe("subagent");
    expect(kindOf("anything", { "hermes.span_kind": "skill" })).toBe("skill");
    expect(kindOf("anything", { "hermes.span_kind": "approval" })).toBe("approval");
    expect(kindOf("zzz")).toBe("other");
  });
  it("formats durations and tokens the way the cards show them", () => {
    expect(fmtDurationMs(0.5)).toBe("500µs");
    expect(fmtDurationMs(7.25)).toBe("7.3ms");
    expect(fmtDurationMs(1500)).toBe("1.50s");
    expect(fmtDurationMs(64_240)).toBe("1m 4s");
    expect(fmtDurationMs(null)).toBe("—");
    expect(fmtTokens(42638)).toBe("42.6k");
    expect(fmtTokens(123456)).toBe("123k");
    expect(fmtTokens(999)).toBe("999");
    expect(fmtTokens("x")).toBeNull();
  });
  it("hides only successful MCP pings", () => {
    expect(isMcpKeepalivePing("MCP send ping", false)).toBe(true);
    expect(isMcpKeepalivePing("MCP send ping", true)).toBe(false);
    expect(isMcpKeepalivePing("agent", false)).toBe(false);
  });
});

describe("traceAttrs", () => {
  it("merges the matched spans' attributes, api first", () => {
    const trace = {
      spanSets: [
        {
          spans: [
            { name: "llm.x", attributes: [{ key: "llm.model_name", value: { stringValue: "from-llm" } }, { key: "only.llm", value: { stringValue: "1" } }] },
            { name: "api.x", attributes: [{ key: "llm.model_name", value: { stringValue: "from-api" } }] },
          ],
        },
      ],
    };
    expect(traceAttrs(trace)).toEqual({ "llm.model_name": "from-api", "only.llm": "1" });
    expect(traceAttrs({})).toEqual({});
  });
});
