# summary.md — otel_plan.md at a glance

A TL;DR of `otel_plan.md`. Read that file for full reasoning; this one is the
checklist.

## What the plugin will gain

- One-container local stack (Grafana + Tempo + Prometheus + Loki + collector
  with spanmetrics) as a supported backend.
- Pre-built Grafana dashboard that works out of the box.
- Logs with trace-id correlation (something the PR shipped without).
- Conversation state gauge + per-state duration histogram.
- Tool stall detection (heartbeat + stalled events + counter).
- Subagent visibility if `delegate_task` fires plugin hooks.

## What stays the same

Multi-backend fan-out, yaml+env config, OpenInference + GenAI attribute
dual-convention, orphan sweep, per-backend BatchSpanProcessor, cache-token
accounting, nested span topology (tool under api under session).

## The 8 phases

| # | Phase | Effort | Files |
|---|---|---|---|
| 1 | LGTM docker-compose + collector config | 0.5d | `docker-compose/lgtm.yaml` (new), `docker-compose/lgtm/otelcol-config.yaml` (new), `docker-compose/all.sh`, `README.md` |
| 2 | Grafana dashboard — rewrite PR's queries against plugin metric names | 1–2d | `docker-compose/lgtm/grafana-dashboards/hermes.json` (new), `scripts/build_dashboard.py` (optional) |
| 3 | Logs via OTel `LoggingHandler` + optional Promtail template | 2d | `log_handler.py` (new), `tracer.py`, `plugin_config.py`, `backends.py` |
| 4 | State machine (idle / thinking / tool_executing) + active-conversations gauge | 1.5d | `session_state.py`, `tracer.py`, `hooks.py` |
| 5 | Tool stall detection with **one** sweeper thread | 1d | `tracer.py`, `plugin_config.py`, `hooks.py` |
| 6 | Enrich `delegate_task` as subagent spans (contingent on plugin hooks firing) | 0.5d | `hooks.py`, `helpers.py` |
| 7 | Gateway lifecycle events (shutdown, drain, inactivity) | deferred | requires upstream hermes-agent hook |
| 8 | Polish — secret redaction in tool args, error heuristic fallback | 0.5d | `helpers.py`, `plugin_config.py`, `hooks.py` |

**Total realistic effort:** ~8 days for phases 1–6. Phase 7 deferred.

## Suggested execution order

1. Phase 1 (LGTM stack)
2. Phase 2 (dashboard)
3. Phase 8 (polish quick wins)
4. Phase 4 (state machine)
5. Phase 5 (stall detection)
6. Phase 3 (logs)
7. Phase 6 (subagents, if hooks exist)

## Explicitly NOT adopting from the PR

- Env-var-only config — yaml+env precedence is better.
- Flat span topology — plugin's nested topology is more informative.
- Global `_state_shims` list — leaks.
- Manual `span.__enter__/__exit__` — fragile.
- Hardcoded query-type buckets like `cob` — too deployment-specific.
- 130-line setup-hermes.sh Promtail auto-install — out of scope.

## Four decisions needed from you

1. **Dashboard:** check-in JSON, or generate from a Python script?
2. **Log handler:** opt-in or opt-out? (Changes the root logger — invasive.)
3. **State 3 `waiting_for_user`:** worth proposing an upstream `on_clarify` hook?
4. **LGTM spanmetrics dimensions:** the PR uses `user_id` / `platform` /
   `profile` — which substitute dimensions (from plugin-emitted attributes)
   should replace them?
