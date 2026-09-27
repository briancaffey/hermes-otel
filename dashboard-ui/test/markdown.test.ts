// The safe markdown parser (src/markdown.ts).
import { describe, expect, it } from "vitest";
import { inlineText, parseBlocks, parseInline } from "../src/md";

describe("parseInline", () => {
  it("tokenises code, bold, italic, links and bare urls", () => {
    const nodes = parseInline("use `ls` and **bold** or *it* see [docs](https://x.y/z) https://a.b/c");
    expect(nodes.map((n) => n.t)).toEqual(["text", "code", "text", "strong", "text", "em", "text", "link", "text", "link"]);
    expect(inlineText(nodes)).toBe("use ls and bold or it see docs https://a.b/c");
  });
  it("keeps unbalanced markers and unsafe links as text", () => {
    expect(parseInline("a * b ** c")).toEqual([{ t: "text", v: "a * b ** c" }]);
    expect(parseInline("[x](javascript:alert(1))")).toEqual([{ t: "text", v: "[x](javascript:alert(1))" }]);
  });
});

describe("parseBlocks", () => {
  it("builds headings, paragraphs, lists, fences, quotes, tables and rules", () => {
    const src = [
      "# Title",
      "",
      "Para one",
      "continues.",
      "",
      "- a",
      "- b",
      "  more of b",
      "",
      "1. one",
      "2) two",
      "",
      "```python",
      "print('x')",
      "```",
      "",
      "> quoted",
      "",
      "| k | v |",
      "|---|---|",
      "| a | 1 |",
      "",
      "---",
    ].join("\n");
    const blocks = parseBlocks(src);
    expect(blocks.map((b) => b.t)).toEqual(["heading", "paragraph", "list", "list", "code", "quote", "table", "rule"]);
    expect((blocks[2] as any).items.length).toBe(2);
    expect(inlineText((blocks[2] as any).items[1])).toBe("b more of b");
    expect((blocks[3] as any).ordered).toBe(true);
    expect(blocks[4]).toEqual({ t: "code", lang: "python", text: "print('x')" });
    expect((blocks[6] as any).rows).toHaveLength(1);
  });
  it("closes an unterminated fence at the end of input", () => {
    expect(parseBlocks("```\nraw")).toEqual([{ t: "code", lang: "", text: "raw" }]);
  });
});
