// Cost and tokens per agent (#447): the root keeps the parent's own calls, each
// top-level subagent.* span carries its child's whole subtree.
import { describe, expect, it } from "vitest";
import { agentBreakdown, topLevelSubagents, rowFromLive, rowFromBackend, NamedAttrs } from "../src/lib";
import { deriveStats } from "../src/live";
import { act } from "react";
import { createRoot } from "react-dom/client";
import React from "react";
import "./sdk-shim";
import { TraceHeader } from "../src/detail";

(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;

const root = {
  "hermes.session_id": "p",
  "gen_ai.request.model": "parent-model",
  "gen_ai.usage.total_tokens": 100,
  "hermes.turn.api_call_count": 2,
  "hermes.cost.usage": 0.01,
};

const sub = (role: string, parent: string, child: string, extra: Record<string, any> = {}): NamedAttrs => ({
  name: `subagent.${role}`,
  attributes: {
    "hermes.subagent.role": role,
    "hermes.subagent.parent_session_id": parent,
    "hermes.subagent.child_session_id": child,
    ...extra,
  },
});

const spans: NamedAttrs[] = [
  { name: "agent", attributes: root },
  { name: "api.parent-model", attributes: { "gen_ai.usage.total_tokens": 100 } },
  sub("planner", "p", "c1", {
    "gen_ai.request.model": "child-model",
    "hermes.subagent.usage.total_tokens": 500,
    "hermes.subagent.api_call_count": 4,
    "hermes.subagent.cost.usage": 0.05,
  }),
  { name: "agent", attributes: { "hermes.session_id": "c1", "gen_ai.usage.total_tokens": 300 } },
  sub("coder", "c1", "g1", { "hermes.subagent.usage.total_tokens": 200, "hermes.subagent.api_call_count": 2, "hermes.subagent.cost.usage": 0.02 }),
  sub("researcher", "p", "c2", {
    "hermes.subagent.usage.total_tokens": 50,
    "hermes.subagent.api_call_count": 1,
    "hermes.subagent.cost.usage": 0.005,
    "hermes.subagent.status": "failed",
  }),
];

describe("topLevelSubagents", () => {
  it("leaves out delegations a delegation made", () => {
    expect(topLevelSubagents(spans).map((s) => s.attributes["hermes.subagent.role"])).toEqual(["planner", "researcher"]);
  });
});

describe("agentBreakdown", () => {
  it("is null when the turn delegated nothing", () => {
    expect(agentBreakdown(root, [{ name: "agent", attributes: root }])).toBeNull();
  });

  it("puts the turn's own figures first, then each top-level delegation", () => {
    const b = agentBreakdown(root, spans)!;
    expect(b.rows.map((r) => [r.label, r.model, r.tokens, r.calls, r.cost, r.error])).toEqual([
      ["main", "parent-model", 100, 2, 0.01, false],
      ["planner", "child-model", 500, 4, 0.05, false],
      ["researcher", null, 50, 1, 0.005, true],
    ]);
    // the grandchild (coder, 200) is inside planner's 500, never added again
    expect(b.total).toEqual({ tokens: 650, calls: 7, cost: 0.065 });
  });

  it("has no cost total unless every row is priced", () => {
    const unpriced = spans.map((s) =>
      s.attributes["hermes.subagent.role"] === "researcher" ? sub("researcher", "p", "c2", { "hermes.subagent.usage.total_tokens": 50 }) : s
    );
    const b = agentBreakdown(root, unpriced)!;
    expect(b.total.cost).toBeNull();
    expect(b.total.tokens).toBe(650);
  });

  it("names a role-less delegation from its span name, else 'subagent'", () => {
    const b = agentBreakdown(root, [
      { name: "agent", attributes: root },
      { name: "subagent.writer", attributes: { "hermes.subagent.parent_session_id": "p", "hermes.subagent.child_session_id": "x" } },
      { name: "subagent", attributes: { "hermes.subagent.parent_session_id": "p", "hermes.subagent.child_session_id": "y" } },
    ])!;
    expect(b.rows.map((r) => r.label)).toEqual(["main", "writer", "subagent"]);
  });
});

describe("rows and live stats", () => {
  const live = (tokens: number, subagents: any) =>
    rowFromLive({
      traceId: String(tokens),
      rootName: "agent",
      rootKind: "agent",
      service: "hermes",
      startNs: 1,
      endNs: 2,
      durationMs: 1,
      spanCount: 3,
      model: null,
      tokens,
      cost: 0.01,
      error: false,
      session: "s",
      subagents,
    });

  it("carries the live store's split; a backend card has none", () => {
    expect(live(100, { count: 2, tokens: 550, calls: 5, cost: 0.055 }).subagents).toEqual({ count: 2, tokens: 550, calls: 5, cost: 0.055 });
    expect(live(100, undefined).subagents).toBeNull();
    expect(rowFromBackend({ traceID: "t", rootTraceName: "agent" }).subagents).toBeNull();
  });

  it("adds the delegations back into the window's totals", () => {
    const stats = deriveStats([live(100, { count: 2, tokens: 550, calls: 5, cost: 0.055 }), live(10, null)]);
    expect(stats.tokens).toBe(660);
    expect(stats.cost).toBeCloseTo(0.075);
  });
});

// Rendered: the header shows the table only for a turn that delegated.

describe("TraceHeader by-agent table", () => {
  const mount = (s: NamedAttrs[]) => {
    const el = document.createElement("div");
    document.body.appendChild(el);
    const r = createRoot(el);
    act(() => {
      r.render(
        React.createElement(TraceHeader, { title: "agent", traceId: "t", durationMs: 1000, rootAttrs: root, spans: s, source: "live", onBack: () => {} })
      );
    });
    const text = el.textContent || "";
    act(() => r.unmount());
    el.remove();
    return text;
  };

  it("lists main, the top-level delegations and the whole run", () => {
    const text = mount(spans);
    expect(text).toContain("by agent");
    expect(text).toContain("tokens (main)");
    expect(text).toContain("planner");
    expect(text).toContain("researcher");
    expect(text).toContain("whole run");
    expect(text).not.toContain("coder");
  });

  it("is absent for a turn without delegations", () => {
    const text = mount([{ name: "agent", attributes: root }]);
    expect(text).not.toContain("by agent");
    expect(text).not.toContain("(main)");
  });
});
