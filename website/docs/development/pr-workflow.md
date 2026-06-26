---
sidebar_position: 5
title: "PR workflow"
description: "The repeatable recipe for turning an issue into a green, well-documented pull request — also available as a Claude Code skill."
---

# PR workflow

This project ships a repeatable workflow for taking a change from issue to
merged PR. It's written down in two places that stay in sync:

- **For contributors:** the checklist below.
- **For Claude Code:** a committed skill at
  [`.claude/skills/hermes-otel-pr/SKILL.md`](https://github.com/briancaffey/hermes-otel/blob/main/.claude/skills/hermes-otel-pr/SKILL.md).
  Because it lives in the repo, anyone who opens this project in Claude Code gets
  the same workflow automatically — no per-session reminders needed. It triggers
  whenever you ask the agent to work on an issue or a PR here.

## The recipe

1. **Branch** off up-to-date `main` (`feat/…`, `fix/…`, `docs/…`). Never commit
   features straight to `main`.
2. **Implement** following the plugin's conventions:
   - New hook → add to `provides_hooks` in `plugin.yaml` and register it in
     `__init__.py` inside the forward-compatible `try/except` loop.
   - Handlers in `hooks.py` accept `**kwargs` and **fail open** (guard on
     `tracer.is_enabled`, never raise into the agent loop).
   - Emit **dual-convention** attributes (OpenInference for Phoenix + `gen_ai.*`
     for Langfuse). Put pure logic in `helpers.py` so it's testable without OTel.
   - Real failures → `ERROR`; benign/unknown outcomes stay `OK`.
3. **Test** with the in-memory fixtures (`inmemory_otel_setup`,
   `two_exporter_pipeline`, `inmemory_otel_with_metrics`). Keep tests
   deterministic — no wall-clock/`perf_counter()`-epoch assumptions (see
   [Testing](/development/testing)).
4. **Run the exact CI checks locally** before pushing:
   ```bash
   uv run --extra dev ruff check .
   uv run --extra dev black --check .
   uv run --extra dev pytest --cov=hermes_otel --cov-fail-under=85
   ```
   The coverage gate is 85%. `black --check` is part of CI — don't skip it.
5. **Update docs in the same PR** (they're acceptance criteria): the relevant
   pages under `website/docs/` (hooks, span-attributes, span-hierarchy,
   limitations) plus `README.md`. Then `cd website && npm run build` and confirm
   it's clean.
6. **(Observability changes) verify against a real backend** — capture a
   before/after in Phoenix or Langfuse to prove the change does what it claims.
7. **Open the PR** with a body that starts with `Closes #<issue>`, using
   conventional-commit titles so [release-please](/development/releasing) can cut
   the version. Confirm CI is green (`gh pr checks <n>`).

See the [skill file](https://github.com/briancaffey/hermes-otel/blob/main/.claude/skills/hermes-otel-pr/SKILL.md)
for the fully detailed version, including the Phoenix GraphQL query and the
`gh api` workaround for editing PR bodies.
