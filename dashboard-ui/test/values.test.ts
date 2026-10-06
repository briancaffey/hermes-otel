// What an attribute value is, and the per-key rules (src/values.ts).
import { describe, expect, it } from "vitest";
import { classify, fmtCount, looksLikeMarkdown, parseChat, parsePyRepr, parseToolCalls, splitList, splitToolResult, turnTools } from "../src/values";

const MESSAGES = JSON.stringify([
  { role: "system", content: "You are Hermes." },
  { role: "user", content: "Run `ls`." },
  {
    role: "assistant",
    content: null,
    tool_calls: [{ id: "call-1", type: "function", function: { name: "terminal", arguments: '{"command": "ls"}' } }],
  },
  { role: "tool", tool_call_id: "call-1", content: '{"output": "a\\nb", "exit_code": 0}' },
]);

describe("parseChat", () => {
  it("reads OpenAI-style messages with tool calls and tool results", () => {
    const msgs = parseChat(MESSAGES)!;
    expect(msgs.map((m) => m.role)).toEqual(["system", "user", "assistant", "tool"]);
    expect(msgs[2].parts).toEqual([{ type: "tool_call", id: "call-1", name: "terminal", args: { command: "ls" } }]);
    expect(msgs[3].toolCallId).toBe("call-1");
  });

  it("reads the flattened Hermes tool-call shape and content parts", () => {
    const msgs = parseChat([
      { role: "assistant", tool_calls: [{ id: "c", name: "read_file", arguments: { path: "/x" } }] },
      {
        role: "user",
        content: [
          { type: "text", text: "hi" },
          { type: "image_url", image_url: { url: "data:..." } },
        ],
      },
    ])!;
    expect(msgs[0].parts[0]).toMatchObject({ type: "tool_call", name: "read_file", args: { path: "/x" } });
    expect(msgs[1].parts[0]).toEqual({ type: "text", text: "hi" });
    expect(msgs[1].parts[1]).toMatchObject({ type: "other", label: "image_url" });
  });

  it("is null for anything that is not a message list", () => {
    expect(parseChat("plain text")).toBeNull();
    expect(parseChat('{"name": "x"}')).toBeNull();
    expect(parseChat([{ id: "c", name: "terminal", arguments: {} }])).toBeNull();
  });
});

describe("parseToolCalls", () => {
  it("accepts the api span's output shape (flattened calls without roles)", () => {
    const calls = parseToolCalls('[{"id": "c1", "name": "terminal", "arguments": {"command": "ls"}, "provider_data": null}]')!;
    expect(calls).toEqual([{ id: "c1", name: "terminal", args: { command: "ls" } }]);
  });
  it("is null for text or objects", () => {
    expect(parseToolCalls("# heading")).toBeNull();
    expect(parseToolCalls('{"output": 1}')).toBeNull();
  });
});

describe("classify", () => {
  it("names each kind the detail view renders", () => {
    expect(classify("input.value", MESSAGES).kind).toBe("messages");
    expect(classify("output.value", '[{"id":"c","name":"t","arguments":{}}]').kind).toBe("tool_calls");
    expect(classify("gen_ai.tool.call.arguments", '{"path": "/x"}').kind).toBe("json");
    expect(classify("output.value", "# Title\n\nbody").kind).toBe("markdown");
    expect(classify("gen_ai.system_instructions", "You are Hermes.").kind).toBe("markdown");
    expect(classify("hermes.tool.command", "ls -la").kind).toBe("command");
    expect(classify("hermes.turn.tools", "read_file,write_file")).toMatchObject({ kind: "list", value: ["read_file", "write_file"] });
    expect(classify("hermes.turn.tool_commands", "echo a|echo b")).toMatchObject({ kind: "list", value: ["echo a", "echo b"] });
    expect(classify("gen_ai.response.finish_reasons", "['tool_calls']")).toMatchObject({ kind: "list", value: ["tool_calls"] });
    expect(classify("hermes.skill.path", "/a/b/SKILL.md").kind).toBe("path");
    expect(classify("hermes.link", "https://phoenix.lan/t/1").kind).toBe("url");
    expect(classify("llm.response.duration_ms", "10251.8")).toMatchObject({ kind: "duration", value: 10251.8 });
    expect(classify("hermes.session.duration_s", "16.3")).toMatchObject({ kind: "duration", value: 16300 });
    expect(classify("gen_ai.usage.total_tokens", "12163")).toMatchObject({ kind: "count", value: 12163 });
    expect(classify("hermes.session.completed", "True")).toMatchObject({ kind: "bool", value: true });
    expect(classify("hermes.session_id", "20260926_185558_9bb336").kind).toBe("id");
    expect(classify("hermes.platform", "cli").kind).toBe("text");
    // a truncated JSON preview is not valid JSON and must not be read as markdown
    expect(classify("output.value", '{"success": true, "content": "# Title\\n\\n**bold**').kind).toBe("code");
    expect(classify("x", "").kind).toBe("empty");
  });
});

describe("lists, python reprs, tool results, turn tools", () => {
  it("splits the plugin's delimited lists and python reprs", () => {
    expect(splitList("hermes.turn.tool_outcomes", "completed")).toEqual(["completed"]);
    expect(parsePyRepr("['a', 'b']")).toEqual(["a", "b"]);
    expect(parsePyRepr("{'ok': True}")).toEqual({ ok: true });
    expect(parsePyRepr("plain")).toBe("plain");
  });
  it("separates a tool result's output from its other fields", () => {
    expect(splitToolResult('{"output": "hello", "exit_code": 1, "success": false}')).toEqual({
      output: "hello",
      rest: { exit_code: 1, success: false },
      error: "failed",
    });
    expect(splitToolResult("just text")).toEqual({ output: "just text", rest: null, error: null });
  });
  it("zips the root span's tools with outcomes, commands and targets", () => {
    expect(
      turnTools({
        "hermes.turn.tools": "terminal",
        "hermes.turn.tool_count": "2",
        "hermes.turn.tool_outcomes": "completed,error",
        "hermes.turn.tool_commands": "echo a|ls /nope",
      })
    ).toEqual([
      { tool: "terminal", outcome: "completed", command: "echo a", target: null },
      { tool: "terminal", outcome: "error", command: "ls /nope", target: null },
    ]);
    expect(turnTools({ "hermes.turn.tools": "read_file,write_file", "hermes.turn.tool_outcomes": "completed" })).toEqual([
      { tool: "read_file", outcome: "completed", command: null, target: null },
      { tool: "write_file", outcome: "completed", command: null, target: null },
    ]);
  });
  it("formats counts and spots markdown", () => {
    expect(fmtCount(1234567)).toBe("1,234,567");
    expect(looksLikeMarkdown("- a\n- b")).toBe(true);
    expect(looksLikeMarkdown("plain sentence.")).toBe(false);
  });
});
