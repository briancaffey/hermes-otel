import { describe, expect, it } from "vitest";
import { kindOf, rowFromBackend, sessionOfCard, traceAttrs } from "../src/lib";
import { ATTRIBUTE_NAMES, KIND_EXAMPLES, KIND_RULES, MODEL_KEYS, SESSION_KEYS, TOTAL_TOKEN_KEYS, TURN_TOTAL_KEYS } from "../src/semconv.generated";

describe("semconv.generated.ts (from hermes_otel/semconv.py)", () => {
  it("kindOf agrees with Python span_kind for every recorded example", () => {
    for (const ex of KIND_EXAMPLES) {
      expect(kindOf(ex.name, ex.attrs), `${ex.name} ${JSON.stringify(ex.attrs)}`).toBe(ex.kind);
    }
  });

  it("the kind table covers every kind the UI knows", () => {
    const kinds = new Set(KIND_RULES.map((r) => r.kind));
    for (const k of ["agent", "cron", "session", "skill", "approval", "human_input", "subagent", "llm", "api", "tool"]) {
      expect(kinds.has(k), k).toBe(true);
    }
  });

  it("key chains are registered attribute names", () => {
    const names = new Set(ATTRIBUTE_NAMES);
    for (const k of [...SESSION_KEYS, ...MODEL_KEYS, ...TOTAL_TOKEN_KEYS, ...TURN_TOTAL_KEYS]) {
      expect(names.has(k), k).toBe(true);
    }
  });

  it("rowFromBackend and sessionOfCard read through the chains", () => {
    const card = {
      traceID: "t1",
      rootTraceName: "agent",
      startTimeUnixNano: "1",
      durationMs: 2,
      spanSets: [
        {
          spans: [
            {
              spanID: "s1",
              name: "agent",
              attributes: [
                { key: "llm.model_name", value: { stringValue: "gpt-4" } },
                { key: "llm.token_count.total", value: { intValue: "42" } },
                { key: "session_id", value: { stringValue: "sess-9" } },
              ],
            },
          ],
        },
      ],
    };
    const row = rowFromBackend(card);
    expect(row.model).toBe("gpt-4");
    expect(row.tokens).toBe(42);
    expect(sessionOfCard(card)).toBe("sess-9");
    expect(traceAttrs(card)["session_id"]).toBe("sess-9");
  });
});
