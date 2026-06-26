---
name: hermes-otel-validate
description: >-
  Prove a hermes-otel change actually emits the span / metric / attribute you
  intended, end-to-end through a real Hermes run into a real backend. Use after
  implementing any telemetry feature, or to capture before/after evidence for a
  PR. Covers choosing a model that triggers your hook, running `hermes -z`,
  reading the plugin debug.log (the fastest signal), force-flush timing, the
  metric-ingest/staleness lag, and querying the backend to assert the telemetry
  landed. Pairs with hermes-otel-backends (which stands up the backend).
---

# Validating a hermes-otel change end-to-end

Unit and integration tests prove the plugin emits the right thing when *handed*
the right input. This skill proves the **whole chain** — Hermes hook → plugin →
OTLP → backend — actually delivers it. That's where the surprises live (a hook
that doesn't fire, a host that drops a field, a backend that mangles a name).

## The loop

1. **Stand up a backend** → use `hermes-otel-backends`. For metrics, OpenObserve
   or LGTM; for spans, anything (Phoenix is nice for LLM spans).
2. **Make the plugin export to it** → set it in the plugin `config.yaml`, with a
   unique `project_name` per run so you can isolate it in the UI.
3. **Pick a model + prompt that triggers your feature** (see below).
4. **Run a turn with debug on** and capture it.
5. **Read the plugin debug.log** — the fastest confirmation the plugin received
   what you expected, before any backend round-trip.
6. **Query the backend** and assert the span/metric/attribute is there.
7. For a PR, do it **twice** — once on `main` (the gap) and once on your branch
   (the fix) — and paste both into the PR.

## 3 · Trigger the feature you're testing

The whole run is only meaningful if your hook actually fires. Match the prompt
to the feature:

| Testing… | Make the model… | Model hint |
|---|---|---|
| reasoning tokens | think | a reasoning model (Groq `qwen3.x`, OpenRouter `nvidia/nemotron-*`) — confirm it reports `completion_tokens_details.reasoning_tokens` |
| skill spans (`skill.*`) | call `skill_view` | any model; prompt it to use an installed skill, or just `skill_view("<name>")` |
| tool spans / metrics | run tools | "list the files here, then read README" |
| api errors | fail | point at a bad key / rate-limited free tier |

Fast > smart: a small fast model (Groq ~500 tok/s) iterates far quicker than a
slow 550B free model. The model is configured in `~/.hermes/config.yaml` under
`model:` (`default`, `provider`, `base_url`, `api_key`, `api_mode`). **Back it
up before editing and restore it after** — and rotate any key you pasted.

## 4 · Run a turn with debug on

```bash
HERMES_OTEL_DEBUG=true hermes -z "PROMPT THAT TRIGGERS YOUR HOOK"
```

`hermes -z` is a non-interactive one-shot (auto-approves, prints the final
answer). It reads `config.yaml` fresh, so a running gateway doesn't interfere.
Reasoning/large models are slow — run it in the background and poll.

> The live plugin Hermes loads is `~/.hermes/plugins/hermes_otel`. When that's a
> git checkout, **switching branches swaps the live plugin** — that's how you do
> before/after: capture on `main`, then on your branch.

## 5 · Read the plugin debug.log (fastest signal)

`HERMES_OTEL_DEBUG=true` writes per-span detail to
`~/.hermes/plugins/hermes_otel/debug.log` (NOT stdout — stdout only shows the
startup banner + the agent's answer). Grep it for what your feature touches:

```bash
grep -nE "usage=|skill span opened|API span ended|post_api_request" \
  ~/.hermes/plugins/hermes_otel/debug.log | tail
```

e.g. `ending span: key=api:... usage={... 'reasoning_tokens': 34 ...}` confirms
the plugin *received* reasoning tokens — proving any zero downstream is a
backend issue, not a plugin bug.

## 6 · Query the backend

Metrics export periodically; the plugin force-flushes on `on_session_end`, so
once the turn ends the data is pushed. Then mind two lags:

- **Ingest delay** (~5–15s) before a just-pushed metric is queryable.
- **Staleness** (~5 min): once the Hermes process exits, instant queries at
  "now" return nothing — widen the time range or keep emitting.

Querying mechanics (histogram `_sum`/`_count`, name mangling, Phoenix GraphQL)
live in `hermes-otel-backends` §5. Assert the **specific** value/label, e.g.:

```bash
# metric present with the right dimension
curl -s 'http://localhost:9090/api/v1/query' \
  --data-urlencode 'query=gen_ai_client_token_usage_sum' | python3 -m json.tool

# span present with your new attribute (Phoenix)
# → query GraphQL, find your span by name, check attributes JSON
```

## Definition of done

- [ ] Plugin `debug.log` shows the hook received the expected payload.
- [ ] Backend UI/API shows the span/metric/attribute with the right value + labels.
- [ ] (For a PR) before (`main`) and after (branch) captured as evidence.
- [ ] Model + `config.yaml` restored; any pasted API key rotated.
- [ ] Synthetic emitters stopped; demo containers torn down (or noted).

## Tip: keep a dashboard live without a slow model

To populate metrics deterministically (no LLM, no rate limits) — e.g. to demo a
graph — drive the plugin hooks directly in the Hermes venv: `tracer.init()`,
fire `on_session_start` / `on_pre_api_request` / `on_post_api_request(usage=...)`
/ `on_session_end`, then `tracer._force_flush()`. Loop every ~20s for a rising
graph. Great for showing someone the UI; not a substitute for a real run.
