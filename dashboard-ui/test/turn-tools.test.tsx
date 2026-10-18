// The root span's tool block (#612): the distinct tools and outcomes show as the
// turn's sets, commands and targets as their own rows, and no row pairs a
// command or target with a tool or outcome the attributes do not tie to it.
import { describe, expect, it } from "vitest";
import { act } from "react";
import { createRoot } from "react-dom/client";
import React from "react";
import "./sdk-shim";
import { SpanSummary } from "../src/detail";
import type { TreeSpan } from "../src/lib";

(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;

function mount(attrs: Record<string, any>): HTMLElement {
  const span: TreeSpan = { spanId: "s", parentSpanId: null, name: "agent", startNs: 0, endNs: 1, durationMs: 1, status: null, _attrs: attrs, children: [] };
  const el = document.createElement("div");
  document.body.appendChild(el);
  const r = createRoot(el);
  act(() => r.render(React.createElement(SpanSummary, { span })));
  return el;
}

describe("turn summary tool block (#612)", () => {
  it("shows the sets once and each command and target on its own row", () => {
    const el = mount({
      "hermes.turn.tools": "read_file,terminal",
      "hermes.turn.tool_count": 2,
      "hermes.turn.tool_outcomes": "completed,error",
      "hermes.turn.tool_commands": "echo a|ls /nope",
      "hermes.turn.tool_targets": "/etc/x|/tmp/y",
    });
    const rows = Array.from(el.querySelectorAll("dl.otel-kv-table dt")).map((dt) => [dt.textContent, dt.nextElementSibling?.textContent]);
    expect(rows).toEqual([
      ["used", "read_fileterminal"],
      ["outcomes", "completederror"],
      ["command", "echo a"],
      ["command", "ls /nope"],
      ["target", "/etc/x"],
      ["target", "/tmp/y"],
    ]);
    expect(el.textContent).toContain("tools · 2");
    el.remove();
  });
});
