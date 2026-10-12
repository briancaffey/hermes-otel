---
sidebar_position: 11
title: "Profiles, multiplexed gateways and A2A"
description: "Running hermes-otel across Hermes profiles: per-profile install and settings, what a multiplexed gateway does with each profile's telemetry, the hermes.profile attribute and label, and how A2A turns forwarded to another profile get their configuration."
---

# Profiles, multiplexed gateways and A2A

Hermes can run several agents from one installation: each **profile** has its own home under `~/.hermes/profiles/<name>` with its own config, skills, plugins and history, and a **multiplexed gateway** can serve every profile from one process. This page says what that means for telemetry.

## Install the plugin per profile

Hermes discovers plugins from each profile's own home, so a profile that has not had the plugin installed and enabled runs with no telemetry at all, and nothing tells you. Install and enable it in every profile you want traced:

```bash
hermes -p <name> plugins install hermes_otel        # from the catalog, or briancaffey/hermes-otel/hermes_otel
hermes -p <name> plugins enable hermes_otel
```

`hermes -p <name> plugins list` shows what that profile has. The default profile is `~/.hermes` itself; `hermes plugins …` without `-p` acts on it.

## Settings are per profile too

The plugin reads its configuration from the home of the profile it runs in, in the usual order: `HERMES_OTEL_CONFIG`, then `<home>/hermes_otel.yaml`, then the legacy copy in the plugin directory. Environment variables (`OTEL_*`, `HERMES_OTEL_*`) come from that profile's `.env`. So:

- Put a `hermes_otel.yaml` (or the `OTEL_*` variables in `.env`) in each profile's home. A profile without either exports nothing and keeps only its live store.
- Give each profile its own `project_name` when they share a backend, so Phoenix or Langfuse show one project per agent. The `hermes.profile` attribute (below) separates them even when they share one.
- The [live store](/dashboard#the-live-store), the debug log and the plugin's `config.yaml` all live under that profile's home: `<home>/hermes_otel_live.db`, `<home>/plugins/hermes_otel/debug.log`.

The [skill's terminal tool](/skill) reads the same home. `otel.py status` prints the home and profile it resolved, and `--db` points it at another profile's store.

## What a multiplexed gateway does

With `gateway.multiplex_profiles` on, one gateway process serves the default profile and every named profile. Hermes loads the plugin once **per profile** in that process, each copy bound to its profile's home, and dispatches a turn's hooks to the copy that belongs to the profile the message was routed to. Since 1.14 the plugin resolves its home through Hermes's scope-aware resolver rather than the process's `HERMES_HOME`, so in a multiplexed gateway:

- each profile's copy reads its own `hermes_otel.yaml`, writes its own live store and debug log, and exports to its own backends;
- every root `agent` / `cron` span, the turn summary and every metric carry the profile's name;
- the profiles never share a tracer, a queue or a store, and one profile's failure to export never touches another's.

Before 1.14 every copy resolved the default profile's home: they shared one config file and one live store, and nothing said which agent a trace came from.

One gateway per profile (`multiplex_profiles: false`, or `hermes -p <name> gateway run`) is a separate process per profile with `HERMES_HOME` set, which was already isolated.

## The `hermes.profile` attribute and label

| Where | Key | Value |
|---|---|---|
| Root span (`agent`, `cron`), at start and at end | `hermes.profile` | `default`, the profile's id (`coder`, `research-2`), or `custom` for a home outside `~/.hermes/profiles/` |
| Resource (every span, metric and log the process exports) | `hermes.profile` | same |
| Every metric | `profile` label | same |

The name is the one `hermes profile list` shows, derived the way Hermes derives it (`hermes_cli.profiles.get_active_profile_name`). It is a bounded label: one value per plugin instance, so it does not add cardinality.

Filter by it wherever the backend allows: `hermes.profile = "coder"` on spans in Phoenix; `profile="coder"` on any metric in Prometheus-style backends; the [dashboard tab](/dashboard) lists it in each trace's attributes. The terminal tool's `traces --text coder` matches it in the live store, and `stats` per profile is one store per profile anyway.

## One service per profile {#one-service-per-profile}

By default every profile exports as one service, `service.name = hermes-agent`, so a backend's service picker and service map show a single node for an orchestrator and all of its workers; the profile is only an attribute filter. To have each named profile show up as its own service, turn on `service_name_from_profile` in each profile's `hermes_otel.yaml` (settings are per profile, see above) or set `HERMES_OTEL_SERVICE_NAME_FROM_PROFILE=true` in its environment:

```yaml
service_name_from_profile: true
```

| Profile | `service.name` | `service.namespace` |
|---|---|---|
| Off (the default), any profile | `hermes-agent` | not set |
| On, a named profile (`coder`) | `coder` | `hermes-agent` |
| On, `default` or `custom` | `hermes-agent` | not set |

- `resource_attributes.service.name` in the same file still wins, as it does today.
- `service.instance.id` stays one per process, so two processes of one profile still write separate metric series.
- The namespace is only set in this mode on purpose: Prometheus-style backends fold it into the `job` label (`hermes-agent/coder`), and setting it on every install would rename every existing series.
- The [dashboard tab](/dashboard) scopes its backend queries to the service of the profile you are viewing (`?profile=coder` queries `coder`), so set the option for the dashboard's profile too. A backend entry's own `service_name` still pins one.
- Dashboards and saved queries that filter on `service.name = "hermes-agent"` (the examples in each `docker-compose/<backend>/README.md` do) need the profile's name, or a filter on `service.namespace = "hermes-agent"` to see the whole fleet.

Independently of the option, the turn root's `gen_ai.agent.name` is the active named profile (`coder`) instead of the constant `hermes-agent`, so GenAI agent views (Weave's Agents tab, Azure's Agents view) tell the agents of a fleet apart. The default and custom profiles keep `hermes-agent`, and a host that passes `agent_name` still wins.

## A2A: turns forwarded to another profile

The [A2A platform](https://github.com/NousResearch/hermes-agent/tree/main/plugins/platforms/a2a) can route an inbound task to a named profile. Hermes then runs that turn as a **fresh process** with that profile's environment (`hermes chat --source a2a` under the target profile's home). Two consequences:

- The turn is traced by the *target* profile's plugin, with the target profile's settings. Variables that live only in the default profile's `.env` do not carry over; put the backend settings in the target profile too.
- The trace is a new one. Linking it to the calling agent's `tool.a2a_call` span needs a `traceparent` header on the outbound call and its inbound counterpart, which Hermes does not yet expose to plugins; [#173](https://github.com/briancaffey/hermes-otel/issues/173) tracks that upstream hook. Until then, correlate by time and by the `hermes.profile` values on both sides.

Tasks handled by the default profile in the live gateway session are traced by the default profile's plugin as usual, with `hermes.platform = a2a`.

## Checking a profile

```bash
hermes -p <name> plugins list                       # plugin present and enabled?
hermes -p <name> -z "say hi"                        # one turn
python3 ~/.hermes/profiles/<name>/plugins/hermes_otel/skills/observability/scripts/otel.py status
python3 ~/.hermes/profiles/<name>/plugins/hermes_otel/skills/observability/scripts/otel.py trace last --attrs | grep hermes.profile
```

`status` names the home and profile the tool resolved and the backends configured for it; `trace last --attrs` shows `hermes.profile` on the root span.
