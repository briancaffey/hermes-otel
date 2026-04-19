---
sidebar_position: 4
title: "Turn summary"
description: "Rolled-up turn-level attributes on the session root span so dashboards don't need to JOIN across children."
---

# Turn summary

At `on_session_end`, the plugin enriches the `session.*` / `cron` root span with a summary of what happened during the turn. Everything a dashboard would otherwise have to JOIN across children to compute is precomputed and attached to the root:

| Attribute | Type | Meaning |
|---|---|---|
| `hermes.turn.tool_count` | int | Distinct tool names invoked |
| `hermes.turn.tools` | string | Sorted CSV of distinct tool names (≤500 chars) |
| `hermes.turn.tool_targets` | string | `\|`-joined distinct file paths / URLs |
| `hermes.turn.tool_commands` | string | `\|`-joined distinct shell commands |
| `hermes.turn.tool_outcomes` | string | Sorted CSV of distinct outcome statuses |
| `hermes.turn.skill_count` | int | Distinct skills inferred |
| `hermes.turn.skills` | string | Sorted CSV of distinct skill names |
| `hermes.turn.api_call_count` | int | `pre_api_request` hooks fired during the turn |
| `hermes.turn.final_status` | string | `completed` · `interrupted` · `incomplete` · `timed_out` |

Empty / zero aggregators are **omitted** rather than emitted as empty strings — so a turn that didn't call any tools simply won't have `hermes.turn.tool_count` on it.

## Why?

The individual `tool.*` spans carry the names, targets, and commands already — so why copy them up?

Because every backend dashboard you'd want to write looks like this:

> "Show me the last 100 turns, how many tools each one used, and what tools they were."

Without the roll-up, that's a JOIN of 100 root spans against all their descendants across an indexed attribute, pivoted into a list. Every backend UI handles that join differently, and many handle it *badly*. With the roll-up, the same question is a table view of 100 rows with the `tools` column right there.

Same logic for:

- "Which turns touched `credentials.json`?" → filter on `hermes.turn.tool_targets CONTAINS "credentials.json"`
- "Which turns ran `rm`?" → filter on `hermes.turn.tool_commands CONTAINS "rm"`
- "Which turns errored?" → filter on `hermes.turn.tool_outcomes CONTAINS "error"` or `hermes.turn.final_status = "error"`

All become single-span filters on the root instead of span-tree traversals.

## The `final_status` values

| Value | Meaning |
|---|---|
| `completed` | Turn finished normally via `on_session_end` |
| `interrupted` | User cancelled mid-turn (Ctrl-C, `/cancel`, etc.) |
| `incomplete` | Turn ended without a final assistant response (edge case) |
| `timed_out` | Orphan-sweep finalized a stale session — see [Orphan-span sweep](/architecture/orphan-sweep) |

`completed` and `timed_out` both map the span `StatusCode` to `OK`. `interrupted` also stays `OK` (user interrupts aren't errors). Only actual tool/API errors inside the turn raise `StatusCode.ERROR` — at the child span level, not the root.

This matters because every backend has a "error rate" widget that reads `StatusCode`. You don't want orphan-sweeps or user interrupts polluting that number.

## Char caps

`hermes.turn.tools` is capped at 500 characters. After a handful of distinct tool names, the CSV gets truncated with `...`. That rarely matters — a turn with more than 10 distinct tool names is unusual — but it prevents pathological cases from blowing up the attribute size.

`hermes.turn.tool_targets` and `hermes.turn.tool_commands` are pipe-joined and not individually capped. A single attribute value is limited by OTel's default attribute length limit (4096 chars); beyond that the SDK truncates.

## Aggregation rule

Distinctness is computed by normalized lowercase string equality. Two `bash` tool calls with the exact same `command` field are collapsed; two with different commands are counted separately. This keeps the rolled-up values bounded even on long-running turns.
