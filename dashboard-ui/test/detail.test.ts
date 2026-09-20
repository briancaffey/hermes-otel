// Detail helpers, URL state and log parameters (#185, #186).
import { describe, expect, it } from "vitest";
import { parseMessages, groupAttrs, headerFacts, prettyJson } from "../src/lib";
import { readNav, navSearch } from "../src/nav";
import { logParams, DEFAULT_LOG_FILTERS } from "../src/params";

describe("parseMessages", () => {
  it("renders a messages array as a conversation", () => {
    const raw = JSON.stringify([{ role: "system", content: "s" }, { role: "user", content: [{ type: "text", text: "hi" }, { type: "text", text: "there" }] }, { role: "assistant", content: null, tool_calls: [{ name: "x" }] }]);
    expect(parseMessages(raw)).toEqual([
      { role: "system", text: "s" },
      { role: "user", text: "hi\nthere" },
      { role: "assistant", text: JSON.stringify([{ name: "x" }], null, 2) },
    ]);
  });
  it("treats a plain string as the user message", () => {
    expect(parseMessages("What is 6 times 7?")).toEqual([{ role: "user", text: "What is 6 times 7?" }]);
    expect(parseMessages(null)).toEqual([]);
    expect(parseMessages("[broken")).toEqual([{ role: "user", text: "[broken" }]);
  });
  it("pretty-prints JSON and leaves text alone", () => {
    expect(prettyJson('{"a":1}')).toBe('{\n  "a": 1\n}');
    expect(prettyJson("plain")).toBe("plain");
    expect(prettyJson({ b: 2 })).toBe('{\n  "b": 2\n}');
  });
});

describe("groupAttrs", () => {
  it("groups by prefix and folds duplicate conventions", () => {
    const g = groupAttrs({
      "gen_ai.request.model": "m",
      "llm.model_name": "m",
      "gen_ai.usage.total_tokens": 5,
      "llm.token_count.total": 5,
      "hermes.session_id": "s",
      "session.id": "s",
      "session_id": "s",
      "tool.name": "t",
      other: 1,
    });
    expect(g.map((x) => x.prefix)).toEqual(["hermes", "gen_ai", "tool", "other"]);
    const genai = g.find((x) => x.prefix === "gen_ai")!;
    expect(genai.entries.map((e) => e.key)).toEqual(["gen_ai.request.model", "gen_ai.usage.total_tokens"]);
    expect(genai.entries[0].aliases).toEqual(["llm.model_name"]);
    expect(g.find((x) => x.prefix === "hermes")!.entries[0].aliases).toEqual(["session.id", "session_id"]);
    expect(g.find((x) => x.prefix === "llm")).toBeUndefined();
  });
});

describe("headerFacts", () => {
  const root = {
    "gen_ai.request.model": "nano",
    "gen_ai.response.model": "ultra:free",
    "gen_ai.usage.total_tokens": 42638,
    "gen_ai.usage.input_tokens": 42000,
    "gen_ai.usage.output_tokens": 638,
    "gen_ai.usage.reasoning.output_tokens": 100,
    "gen_ai.usage.cache_read.input_tokens": 8640,
    "hermes.turn.tools": '["write_file","read_file"]',
    "hermes.turn.exit_reason": "text_response",
    "hermes.turn.final_status": "completed",
    "hermes.session_id": "s-1",
    "hermes.turn.number": "1",
    "hermes.platform": "cli",
  };
  it("reads the turn's facts from the root span", () => {
    const f = headerFacts(root);
    expect(f.requestModel).toBe("nano");
    expect(f.responseModel).toBe("ultra:free");
    expect(f.totalTokens).toBe(42638);
    expect(f.reasoningTokens).toBe(100);
    expect(f.cacheReadTokens).toBe(8640);
    expect(f.cost).toBeNull();
    expect(f.tools).toEqual(["write_file", "read_file"]);
    expect(f.exitReason).toBe("text_response");
    expect(f.session).toBe("s-1");
    expect(f.turn).toBe(1);
    expect(f.platform).toBe("cli");
  });
  it("hides the served model when it equals the request and sums api spans when the root has no total", () => {
    const f = headerFacts({ "gen_ai.request.model": "m", "gen_ai.response.model": "m" }, [{ "gen_ai.usage.total_tokens": 10, "gen_ai.usage.input_tokens": 8, "gen_ai.usage.output_tokens": 2 }, { "gen_ai.usage.total_tokens": 5 }]);
    expect(f.responseModel).toBeNull();
    expect(f.totalTokens).toBe(15);
    expect(f.inputTokens).toBe(8);
  });
});

describe("nav", () => {
  it("round-trips the state through the query string", () => {
    expect(readNav("?tab=traces&source=live&trace=abc&junk=1")).toEqual({ tab: "traces", source: "live", trace: "abc" });
    expect(navSearch({ tab: "logs", trace: "" }, "?tab=traces&trace=abc&view=turns")).toBe("?tab=logs&view=turns");
    expect(navSearch({}, "")).toBe("");
  });
});

describe("logParams", () => {
  it("maps the filters onto the search route with the backend", () => {
    const p = Object.fromEntries(logParams({ ...DEFAULT_LOG_FILTERS, minLevel: "30", logger: "agent", traceId: "t", text: "x", lookback: 6 }, "oo", 200));
    expect(p).toEqual({ lookback_hours: "6", limit: "200", min_level: "30", logger: "agent", trace_id: "t", text: "x", backend: "oo" });
    expect(Object.fromEntries(logParams(DEFAULT_LOG_FILTERS, "live", 50))).toEqual({ lookback_hours: "1", limit: "50" });
  });
});
