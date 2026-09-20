// Search-bar parameter mapping and session grouping (#183, #187).
import { describe, expect, it } from "vitest";
import { DEFAULT_FILTERS, liveParams, backendParams, isDefaultFilters } from "../src/params";
import { groupBySession, sessionOfCard } from "../src/lib";

const F = { ...DEFAULT_FILTERS, status: "error" as const, kind: "tool", tool: "terminal", model: "nano", session: "s1", minDurationMs: "250", text: "hello", lookback: 6 };

describe("liveParams", () => {
  it("maps every field onto /live/traces", () => {
    const p = liveParams(F);
    expect(Object.fromEntries(p)).toEqual({ lookback_hours: "6", limit: "100", status: "error", kind: "tool", tool: "terminal", model: "nano", session: "s1", min_duration_ms: "250", text: "hello" });
  });
  it("sends only the window by default", () => {
    expect(Object.fromEntries(liveParams(DEFAULT_FILTERS))).toEqual({ lookback_hours: "1", limit: "100" });
    expect(isDefaultFilters(DEFAULT_FILTERS)).toBe(true);
    expect(isDefaultFilters(F)).toBe(false);
  });
});

describe("backendParams", () => {
  it("maps onto /traces/search with the backend and widens roots for kind/tool filters", () => {
    const p = Object.fromEntries(backendParams(F, "phx"));
    expect(p.backend).toBe("phx");
    expect(p.roots_only).toBe("false");
    expect(p.name_regex).toBe("^tool\\.");
    expect(p.free_text).toBe("hello");
    expect(p.tool).toBe("terminal");
    expect(p.model).toBe("nano");
    expect(p.session).toBe("s1");
    expect(p.min_duration_ms).toBe("250");
  });
  it("keeps roots only and no backend for the default source", () => {
    const p = Object.fromEntries(backendParams(DEFAULT_FILTERS, "live"));
    expect(p.backend).toBeUndefined();
    expect(p.roots_only).toBe("true");
    expect(p.name_regex).toBeUndefined();
  });
});

describe("groupBySession", () => {
  const card = (id: string, sid: string | null, start: number, tokens: number, err = false) => ({
    traceID: id,
    rootTraceName: "agent",
    startTimeUnixNano: String(start),
    durationMs: 1000,
    spanCount: 3,
    spanSets: [{ spans: [{ name: "agent", attributes: [
      ...(sid ? [{ key: "hermes.session_id", value: { stringValue: sid } }] : []),
      { key: "gen_ai.usage.total_tokens", value: { intValue: String(tokens) } },
      { key: "llm.model_name", value: { stringValue: "m" } },
      ...(err ? [{ key: "status", value: { stringValue: "error" } }] : []),
    ] }] }],
  });
  it("groups cards by their session id with totals from the root attributes", () => {
    const rows = groupBySession([card("a", "s1", 1e18, 100), card("b", "s1", 2e18, 50, true), card("c", "s2", 3e18, 7), card("d", null, 4e18, 1)]);
    expect(rows.map((r) => r.session)).toEqual(["s2", "s1"]);
    const s1 = rows[1];
    expect(s1.turns).toBe(2);
    expect(s1.tokens).toBe(150);
    expect(s1.errors).toBe(1);
    expect(s1.spans).toBe(6);
    expect(s1.traceIds).toEqual(["a", "b"]);
    expect(s1.model).toBe("m");
    expect(s1.cost).toBeNull();
  });
  it("reads Langfuse's session key too", () => {
    expect(sessionOfCard({ spanSets: [{ spans: [{ attributes: [{ key: "langfuse.sessionId", value: { stringValue: "L" } }] }] }] })).toBe("L");
    expect(sessionOfCard({ spanSets: [] })).toBeNull();
  });
});
