---
sidebar_position: 4
title: "Turn summary"
description: "Rolled-up turn-level attributes on the session root span so dashboards don't need to JOIN across children."
---

# Turn summary

At `on_session_end`, the plugin enriches the `agent` / `cron` root span with a summary of what happened during the turn. Everything a dashboard would otherwise have to JOIN across children to compute is precomputed and attached to the root:

| Attribute | Type | Meaning |
|---|---|---|
| `hermes.turn.tool_count` | int | Distinct tool names invoked |
| `hermes.turn.tools` | string | Sorted CSV of distinct tool names (≤500 chars) |
| `hermes.turn.tool_targets` | string | `\|`-joined distinct file paths / URLs, in first-seen order (≤500 chars) |
| `hermes.turn.tool_commands` | string | `\|`-joined distinct shell commands, in first-seen order (≤500 chars) |
| `hermes.turn.tool_outcomes` | string | Sorted CSV of distinct outcome statuses (≤500 chars) |
| `hermes.turn.skill_count` | int | Distinct skills inferred |
| `hermes.turn.skills` | string | Sorted CSV of distinct skill names (≤500 chars) |
| `hermes.turn.api_call_count` | int | Main-loop `pre_api_request` hooks fired during the turn (auxiliary calls are counted separately, as `hermes.turn.auxiliary.*`) |
| `hermes.turn.final_status` | string | `completed` · `interrupted` · `failed` · `incomplete` (from Hermes' flags on `on_session_end`); `timed_out` from the orphan sweep; `finalized` / `reset` when a session boundary closed the root |

Empty / zero aggregators are **omitted** rather than emitted as empty strings — so a turn that didn't call any tools simply won't have `hermes.turn.tool_count` on it.

## Why?

The individual `tool.*` spans carry the names, targets, and commands already — so why copy them up?

Because every backend dashboard you'd want to write looks like this:

> "Show me the last 100 turns, how many tools each one used, and what tools they were."

Without the roll-up, that's a JOIN of 100 root spans against all their descendants across an indexed attribute, pivoted into a list. Every backend UI handles that join differently, and many handle it *badly*. With the roll-up, the same question is a table view of 100 rows with the `tools` column right there.

Same logic for:

- "Which turns touched `credentials.json`?" → filter on `hermes.turn.tool_targets CONTAINS "credentials.json"`
- "Which turns ran `rm`?" → filter on `hermes.turn.tool_commands CONTAINS "rm"`
- "Which turns errored?" → filter on `hermes.turn.tool_outcomes CONTAINS "error"` or `hermes.turn.final_status = "failed"`

All become single-span filters on the root instead of span-tree traversals.

## The `final_status` values

| Value | Meaning |
|---|---|
| `completed` | Turn finished normally via `on_session_end` |
| `interrupted` | User cancelled mid-turn (Ctrl-C, `/cancel`, etc.) |
| `incomplete` | Turn ended without a final assistant response (edge case) |
| `timed_out` | Orphan-sweep finalized a stale session — see [Orphan-span sweep](/architecture/orphan-sweep) |
| `failed` | Hermes reported the turn failed (`on_session_end` with `failed`), or its last API attempt ended in `api_request_error` and the turn never ended (see below) |
| `finalized` / `reset` | A still-open root closed by `on_session_finalize` / `on_session_reset` |

`completed` and `timed_out` both map the span `StatusCode` to `OK`. `interrupted` also stays `OK` (user interrupts aren't errors). `failed` and `incomplete` are `ERROR`; `failed` carries `error.type` from the last API error.

**A turn whose API attempts all fail (#467).** When the provider can't be reached, Hermes fires `api_request_error` for each attempt but neither `post_llm_call` nor `on_session_end`. The root is closed later: by `on_session_finalize` (the CLI's exit), by the orphan sweep, or, in a gateway, when the session's next turn starts. Whichever comes first sees that the last main-loop attempt failed with no success after it and closes the root `ERROR`, `final_status=failed`, `hermes.session.failed=true`, `error.type` from that attempt. The next turn then gets its own trace instead of joining the failed one.

This matters because every backend has a "error rate" widget that reads `StatusCode`. You don't want orphan-sweeps or user interrupts polluting that number.

## Char caps

Every string in the summary (`hermes.turn.tools`, `tool_targets`, `tool_commands`, `tool_outcomes`, `skills`) is capped at 500 characters; a longer list is cut and ends with `...`. That rarely matters for tool names, but a turn that touches many files or runs many commands fills `tool_targets` and `tool_commands` quickly, so the later entries are the ones dropped. The individual `tool.*` spans always carry their own target and command.

## Aggregation rule

Distinctness is exact string equality (no case folding). Two `bash` tool calls with the exact same `command` field are collapsed; two with different commands are listed separately.
