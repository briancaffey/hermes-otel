---
sidebar_position: 5
title: "Tool identity & outcome"
description: "How the plugin infers hermes.tool.target, hermes.tool.command, hermes.tool.outcome, and hermes.skill.name from raw tool args."
---

# Tool identity & outcome

Every `tool.*` span carries the raw args and result — but that's a big JSON blob that's painful to filter on. hermes-otel extracts a few normalized attributes so your dashboards can answer "what did this tool *do*" without parsing.

## The normalized attributes

| Attribute | Extracted from | Example |
|---|---|---|
| `hermes.tool.target` | First non-empty args.`path` · `file_path` · `target` · `url` · `uri` | `/home/user/config.yaml` |
| `hermes.tool.command` | First non-empty args.`command` · `cmd` | `ls -la ~/Downloads` |
| `hermes.tool.outcome` | Result classification | `completed` · `error` · `timeout` · `blocked` |
| `hermes.skill.name` | Inferred from args paths matching `/skills/<name>/` | `git-workflow` |

All are optional — if the input doesn't have them (e.g. a tool with no `path` arg), they're not set.

## `hermes.tool.target`

The first of these keys that has a non-empty string value wins:

1. `path`
2. `file_path`
3. `target`
4. `url`
5. `uri`

Used by `read_file`, `edit_file`, `write_file`, `search_files`, `fetch_url`, `browser_navigate`, and any future tool following the same naming.

Useful dashboard queries:

- "Which turns touched `~/.hermes/.env`?" — filter on `hermes.tool.target CONTAINS ".hermes/.env"`
- "Which files got edited this week?" — group by `hermes.tool.target` where `tool.name = "edit_file"`

## `hermes.tool.command`

For shell-family tools. The first of these keys that has a non-empty string value wins:

1. `command`
2. `cmd`

Useful dashboard queries:

- "How often do we `rm`?" — filter on `hermes.tool.command CONTAINS "rm "`
- "Most-used commands" — group by first word of `hermes.tool.command`

## `hermes.tool.outcome`

Normalised to one of:

- `completed` — tool returned without error
- `error` — tool raised an exception or the result dict had `"status": "error"`
- `timeout` — tool ran longer than its timeout
- `blocked` — tool was denied by a policy (e.g. command approval)

Additional values can appear if the tool result dict has an explicit `status` field — it's lowercased and taken as-is (e.g. `status: "cancelled"`).

The outcome also drives the span's OTel `StatusCode`:

| Outcome | `StatusCode` |
|---|---|
| `completed` | `OK` |
| `error` | `ERROR` |
| `timeout` | `OK` ← intentional |
| `blocked` | `OK` ← intentional |
| *anything else* | `OK` |

Why timeouts and blocks don't map to `ERROR`: they're expected operational conditions, not failures of the tool itself. Treating them as errors would pollute every "error rate" dashboard. If you want to alert on blocked tools separately, filter on `hermes.tool.outcome = "blocked"` directly.

## `hermes.skill.name`

A skill is detected from a tool call two ways, and the name is attached as `hermes.skill.name` with a `hermes.skill.source` of:

- **`skill_view`** — the canonical signal. The `skill_view` tool carries the skill name as an *argument* (`{"name": "git-workflow"}`), so it's read directly. A plugin-namespaced name (`plugin:git-workflow`) is reduced to the bare name.
- **`path_match`** — any tool with a path arg matching `/skills/<name>/`:
  - Matches: `/home/user/.hermes/skills/git-workflow/reference.md` → `git-workflow`
  - Does **not** match: `/home/user/.hermes/optional-skills/ai-tools/references/foo.md` (explicit exclusion — `optional-skills/*/references/` is for reference material, not skill invocation)

Also increments a Prometheus-style counter (and, unless `skill_spans` is off, opens a [`skill.*` span](/architecture/span-hierarchy#skill)):

```text
hermes.skill.inferred{skill_name="git-workflow", source="skill_view"}
```

Useful for:

- Auditing which skills actually get used
- Detecting skills that never fire (dead code)
- Catching skills referenced outside their intended tools

## Aggregated into turn summary

All four normalized attributes are rolled up to the session root in the [turn summary](/architecture/turn-summary) (`hermes.turn.tools`, `hermes.turn.tool_targets`, `hermes.turn.tool_commands`, `hermes.turn.tool_outcomes`, `hermes.turn.skills`).

So "which turns touched `credentials.json`?" works at both:

- Child span level: `hermes.tool.target CONTAINS "credentials.json"`
- Root span level: `hermes.turn.tool_targets CONTAINS "credentials.json"` (cheaper filter; one span per turn instead of many)
