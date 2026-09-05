---
name: pr-review
description: >-
  Review an incoming pull request to hermes-otel the way the maintainer would:
  fetch it into an isolated worktree, run the exact CI gate, install the PR's
  plugin into a scratch HERMES_HOME and drive a real Hermes turn through it,
  judge domain fit (is this OpenTelemetry for Hermes Agent, expressed as
  spans / metrics / logs through the existing pipeline?), poke holes (gateway
  lifecycle, leaks, hot-path cost, privacy, cross-platform), and deliver a
  verdict — accept / accept with changes / decline — plus a draft, encouraging
  comment for the author. Use whenever asked to review, evaluate, test, or
  "give me your take on" a PR or contribution to this repo. Pass the PR number
  or URL as the argument.
---

# Reviewing a hermes-otel PR

You are the maintainer's technical reviewer. The maintainer wants this project
to grow and wants contributors to feel welcome — **and** wants every merge to
keep hermes-otel a coherent OpenTelemetry plugin rather than a grab-bag of
Hermes add-ons. Both goals are served by the same thing: an honest, specific,
evidence-backed review with a clear path to "yes".

Read the sibling skills first when you need their detail: `hermes-otel-pr`
(conventions + exact CI), `hermes-otel-validate` (proving telemetry lands),
`hermes-otel-backends` (standing up / querying a backend).

Work through every section. Measure instead of speculating: a claim in the
review should be backed by a command you ran, a number you observed, or a line
in the diff. Never post anything to GitHub unless the maintainer says so — the
deliverable is a report the maintainer reads *before* deciding.

## 1. Fetch the PR without disturbing the clone

```bash
gh pr view <N> --repo briancaffey/hermes-otel \
  --json title,author,state,headRefName,headRepositoryOwner,body,additions,deletions,changedFiles,commits
cd <clone> && git fetch origin --prune && git fetch origin pull/<N>/head:pr-<N>
git diff --stat origin/main...pr-<N>
git worktree add -f "$SCRATCH/pr<N>-wt" pr-<N>        # test here, never `mv` the clone
git worktree add -f "$SCRATCH/main-wt" origin/main     # baseline for before/after
```

`$SCRATCH` is the session scratchpad. Note whether the head is a fork
(`headRepositoryOwner` ≠ maintainer) — you cannot push fixes to it, so every
requested change must be phrased for the author.

## 2. Read the whole diff — all of it

Dump every non-binary changed file and read it top to bottom. Skimming is how
review misses the one `import`-time side effect. While reading, extract:

- **Surface area:** new modules, new config knobs / env vars, new
  dependencies (runtime vs dev vs optional extras), new files written to disk,
  new processes or threads, new network calls.
- **Integration style:** does it call the plugin's own seams
  (`tracer.start_span` / `end_span` / `record_metric`, `plugin_config`,
  `log_handler`, `live_store`) — or does it wrap / monkeypatch / re-implement
  them? Anything that runs at import time or patches a class attribute is a
  red flag to test, not an automatic reject.
- **Hook contract:** which hooks it touches, whether handlers still accept
  `**kwargs`, fail open, and stay observer-only. Payload fields it relies on
  (e.g. `session_id` in `pre_tool_call` kwargs) — verify against the Hermes
  source (`grep -rn "_dispatch_pre_tool_call_hooks\|invoke_hook(" ~/git/hermes-agent`)
  and `docs/observability/README.md` there.
- **Claims in the PR body** that you can check (e.g. "no extra threads",
  "off by default", "hook fires once per X").
- **Provenance hints:** references to files that don't exist in this repo,
  dashboards not in the PR, vendored screenshots — signs of a port from an
  internal project that may carry assumptions we don't share.

## 3. The domain test — is this OpenTelemetry for Hermes Agent?

hermes-otel's contract: **Hermes lifecycle hooks → OTel spans, metrics and
logs → any OTLP backend.** Everything a PR adds must be locatable in that
sentence. Run every new piece of data the PR produces through this table:

| Question | Passing answer |
|---|---|
| Which OTel signal is this? | A span or span attribute (per-operation facts), a log/event (discrete occurrences), or a metric (aggregates over time). "A CSV", "a PNG", "a JSON file" is not a signal. |
| Does it flow through the existing pipeline? | Emitted via `tracer.start_span`/`end_span`, `tracer.record_metric` (instrument added in `_create_metric_instruments`), or the logs handler — so it fans out to every configured backend (Phoenix, Langfuse, SigNoz, LGTM, OpenObserve…) with no backend-specific work. |
| Semantic conventions? | Existing attrs follow the dual convention (OpenInference `llm.*`/`input.value` **and** `gen_ai.*`). New host/process data should use OTel semconv names (`process.cpu.utilization`, `system.cpu.utilization`, `hw.gpu.utilization`, `hw.gpu.memory.usage`, `hw.power`) with the `hermes.*` namespace only for Hermes-specific things. Check https://opentelemetry.io/docs/specs/semconv/ — don't invent names. |
| Cardinality? | Metric labels stay low-cardinality (model, provider, tool name, status). Never session ids, task ids, or free text on metrics. |
| Configuration surface? | Knobs live in `HermesOtelConfig` (`plugin_config.py`), read from `$HERMES_HOME/hermes_otel.yaml` and `HERMES_OTEL_*` env vars, documented in `config.yaml.example`. Bare `HERMES_*` is Hermes Agent's namespace — a plugin must not squat it. |
| Privacy? | Respects `capture_previews`, the `*_preview_max_chars` clips, `capture_full_*`, `capture_sender_id`. Anything that writes prompts / tool args / tool output somewhere new must go through the same gates. |
| Backend-agnostic? | No feature that only works with files on the local disk, one UI, or one vendor. If a backend can't render it, that's the backend's limitation to document, not a reason to add a sidecar renderer. |
| Fail-open? | A telemetry failure can never raise into the agent loop, block a hook, or change tool behaviour. |
| Already exists? | Is the thing the PR builds already provided by the OTel Collector (`hostmetrics` receiver, DCGM / AMD SMI exporters, file exporter), by an existing plugin module (`live_store` for local capture, `session_state` for per-turn aggregates), or by an existing attribute? Prefer composing over re-implementing. |

**Hermes runtime realities the PR must survive** (contributors usually test in
one-shot CLI mode and miss these):

- The **gateway is one long-lived process serving many sessions** (Telegram,
  Discord, cron…). Per-session state must be bounded and released;
  `on_session_end` is *turn-scoped* (fires every `run_conversation`), and
  `on_session_finalize`/`on_session_reset` are the identity-lifecycle hooks.
  Anything "stopped at process exit" never stops in a gateway.
- **CWD is arbitrary** (a project dir, `/`, or a launchd default). Relative
  output paths litter the user's project — and the agent's own `ls` will see
  them, changing what the model observes (we saw this happen).
- **Hooks run synchronously in the agent loop.** Work whose cost grows with
  session length (re-reading a growing file per tool call) becomes user-visible
  latency after an hour. Hermes applies hook timeouts; `pre_tool_call` is
  fail-closed on timeout.
- Sessions are **resumed** (`hermes -r`), **parallel tool calls** exist,
  sub-agents nest, and the plugin runs on macOS, Linux, and Windows.
- Crashes (SIGKILL / OOM) skip `atexit`; child processes must notice their
  parent died on their own.

Write down, per new capability: *in-domain as designed* / *in-domain but wrong
shape* (right goal, should be a span attribute / metric / log instead) /
*out of domain* (belongs in a separate tool, a collector, or the backend).

## 4. The mechanical gate — run exactly what CI runs

In the PR worktree:

```bash
uv run --extra dev ruff check .
uv run --extra dev black --check .
uv run --extra dev pytest --cov=hermes_otel --cov-report=term --cov-fail-under=85 -q \
  2>&1 | grep -vE "Transient error|Failed to export|Max retries|Connection refused" | tail -40
uv run --extra dev python scripts/scan_plugin_artifact.py   # compare finding counts with main-wt
git status --porcelain            # `M uv.lock` ⇒ the PR changed deps without re-locking
```

Then the things CI doesn't catch:

- **Dependencies declared?** Every runtime import the artifact needs must be in
  `pyproject.toml` `[project] dependencies` *and* `hermes_otel/requirements.txt`
  (`test_install_artifact.py` keeps them in sync) — "it's in the Hermes venv
  already" is not a declaration. Optional deps import lazily and degrade.
- **Runtime code in `hermes_otel/`**, dev material outside it; nothing new at
  the repo root. Binary assets in the repo need a reason.
- **Docs are acceptance criteria:** `website/docs/reference/span-attributes.md`
  for any new attribute, `hooks.md` for hook changes, a config reference for
  new knobs, `config.yaml.example`, `README.md`, `after-install.md` /
  `plugin.yaml` for new install-time deps, and the bundled
  `hermes_otel/skills/observability/SKILL.md` if users need to know.
- **Tests test the real seams:** drive `hermes_otel.hooks` functions with
  kwargs as Hermes would, use `inmemory_otel_setup`, use `tmp_path` /
  `monkeypatch.setenv`, and never leak env, files, or processes between tests.
- **Conventional commit** titles (`feat:` bumps minor via release-please).

## 5. Install it and drive a real Hermes turn through it

Never edit the real `~/.hermes`. Build a scratch home from it:

```bash
H=$SCRATCH/hermes-home; mkdir -p $H/plugins
cp ~/.hermes/{config.yaml,.env,auth.json,hermes_otel.yaml} $H/
rsync -a --exclude='live.db*' --exclude='__pycache__' $SCRATCH/pr<N>-wt/hermes_otel/ $H/plugins/hermes_otel/
sed -i '' 's/^project_name: .*/project_name: pr<N>-review/' $H/hermes_otel.yaml   # isolate in the backend UI
mkdir -p $SCRATCH/run1 && cd $SCRATCH/run1        # a clean, throwaway CWD — watch what appears in it
HERMES_HOME=$H <PR's env flags> hermes chat -q "<prompt that exercises the feature>" --oneshot -Q --yolo
```

(macOS has no `timeout`; reasoning models take minutes — run in the background
and poll. Enable every opt-in flag the PR adds so the new path actually runs.)

Then look at three places:

1. **The plugin's own record:** `sqlite3 $H/plugins/hermes_otel/live.db
   "select json_extract(data,'$.name'), json_extract(data,'$.attributes.\"<new.attr>\"') from events where kind='span'"`
   and `$H/plugins/hermes_otel/debug.log` if `HERMES_OTEL_DEBUG=true`.
2. **The backend:** Phoenix GraphQL at `https://phoenix.lan/graphql` (or
   whatever `hermes_otel.yaml` points at) filtered to the review project —
   confirm the attribute / span / metric is really there with the right value.
3. **Side effects:** `ls -la $SCRATCH/run1` (files the plugin dropped in CWD),
   `ps -ef | grep <new process>` after Hermes exits (orphans), thread count,
   and anything written outside `$HERMES_HOME`.

If the PR changes existing behaviour, run the same prompt against `main-wt`'s
plugin too and diff the spans.

## 6. Poke holes — measure, don't speculate

Pick the failure modes that apply and write a short script (run it with
`uv run --extra dev --with <extra deps> python …` in the PR worktree) that
produces a number. Patterns that have paid off:

- **Gateway simulation:** call the hooks directly for N sessions
  (`on_session_start` × N, then `on_session_end` × N); count leftover
  processes / threads / dict entries. Anything that only frees at `atexit`
  leaks here.
- **Hot-path cost with the feature OFF:** `timeit` the wrapped vs original
  hook; count syscalls (`builtins.open` shim) per span. The plugin must cost
  users nothing for a feature they didn't enable.
- **Growth over time:** synthesize an hour of data at the feature's sampling
  rate and time the per-call work that reads it.
- **Orphaning:** launch the helper process with a short-lived fake parent pid;
  see whether it exits when the parent does.
- **Self-cost of samplers:** `psutil` CPU time of the helper over 10 s.
- **Concurrency / resume / timezone:** same tool name twice in parallel; a
  second process on the same `session_id`; local-time strings vs epoch.
- **Privacy:** grep every new on-disk artifact for prompt text, tool output,
  sender ids.

Report each as *observed value → consequence for a real user*.

## 7. Verdict and report

Structure the report for a maintainer who will read it once and decide:

1. **What the PR does and what's genuinely good about it** — first, honestly.
   Contributors read this section too; credit the real work.
2. **Verdict:** `accept` · `accept with changes` (list them, ordered; split
   *must* from *nice*) · `decline` (say what a version we *would* accept looks
   like — a decline should still hand the author a path).
3. **Domain-fit assessment** from §3, capability by capability.
4. **Findings**, most severe first, each with the evidence (command, number,
   file:line) and the concrete fix.
5. **What's missing** (docs, deps, tests, semconv) and **what shouldn't be
   added** (out-of-domain pieces, with the better home named).
6. **Draft comment for the author.** Warm, specific, no hedging. Thank them
   for something real, state the verdict plainly, give the ordered change
   list with the *why* (link the design principle), and offer to help split
   or land the acceptable subset. It should read like a maintainer who wants
   the contribution to succeed, not a gate.

Publish the report as an artifact when it's more than a screenful; keep the
terminal message to the verdict and the top findings.

## 8. Clean up

```bash
pkill -f metrics_poller.py 2>/dev/null   # or whatever helper the PR spawns
cd <clone> && git worktree remove --force "$SCRATCH/pr<N>-wt"; git worktree remove --force "$SCRATCH/main-wt"
git branch -D pr-<N>      # optional; keep if follow-up is expected
```

Leave `~/.hermes`, the running gateway, and the clone's original branch as
you found them. Delete the review project from the backend only if asked.
