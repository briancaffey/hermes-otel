---
sidebar_position: 10
title: "Host & GPU metrics"
description: "Correlate an agent's turns and tool calls with CPU and GPU load on the machine it runs on — as OTel metrics and span attributes, on every backend."
---

# Host & GPU metrics

Opt-in sampler that answers *"was that slow tool call the tool, or was the GPU
pegged by inference?"*. When enabled, the plugin samples the Hermes process
tree, the whole host, and any AMD / NVIDIA GPU on a fixed interval and exports
the readings **through the same OTel pipeline as everything else**: as
metrics on every backend that receives metrics, and as average / peak
utilization attributes on each `tool.*` span. Nothing is written to disk.

**Off by default.** It costs one daemon thread per Hermes process (not per
session) and, at the default one-second interval, well under a percent of a
core.

## The switch

```yaml
# hermes_otel.yaml
host_metrics: true
host_metrics_gpu: auto          # auto | amd | nvidia | off
host_metrics_interval_ms: 1000  # sampling cadence; 100–250 for fine-grained tool windows
```

Or via env vars:

```bash
export HERMES_OTEL_HOST_METRICS=true
export HERMES_OTEL_HOST_METRICS_GPU=auto
export HERMES_OTEL_HOST_METRICS_INTERVAL_MS=1000
```

The startup banner confirms it: `✓ Host metrics sampler on (every 1000 ms, gpu=nvidia)`.

## Requirements

| Signal | Needs | Notes |
|---|---|---|
| CPU (process tree + host) | `psutil` | Ships with hermes-agent, so it is already in the venv that runs `hermes`. Without it the sampler logs a warning and stays off. |
| NVIDIA GPU | `pynvml` | `pip install pynvml` into the Hermes venv. |
| AMD GPU | `amdsmi` | Install the version that matches your ROCm stack (AMD recommends the copy under `/opt/rocm/share/amd_smi`). A `rocm-smi` CLI fallback covers SR-IOV VF partitions where the Python API raises. |

GPU detection is automatic (`auto` tries AMD, then NVIDIA); force a vendor with
`host_metrics_gpu`, or set `off` to skip GPU probing entirely on a host without
one.

## What is emitted

### Metrics

Semantic-convention names, so existing host dashboards and alert rules apply.
Observable instruments: the SDK reads the latest sample on every collection,
i.e. at `flush_interval_ms`.

| Metric | Instrument | Unit | Attributes |
|---|---|---|---|
| `process.cpu.utilization` | gauge | `1` | `cpu.mode` = `user` / `system` |
| `system.cpu.utilization` | gauge | `1` | `cpu.mode` = `user` / `system` |
| `hw.gpu.utilization` | gauge | `1` | `hw.id` (`gpu0`, `gpu1`…), `hw.vendor` |
| `hw.gpu.memory.usage` | up-down counter | `By` | `hw.id`, `hw.vendor` |
| `hw.power` | gauge | `W` | `hw.id`, `hw.vendor`, `hw.type=gpu` |

Utilization is a **0..1 ratio normalised by logical core count** (1.0 = every
core busy), as the conventions specify — a single fully-busy core on an
8-core box reads `0.125`. `process.cpu.utilization` covers the Hermes process
**plus every child it spawned** (tool subprocesses, MCP servers it launched);
an external inference server is not a child and is excluded by construction.

The readings are also mirrored into the [live dashboard](/getting-started/installation) store.

### Span attributes

Each `tool.*` span additionally carries the utilization observed while the
tool ran:

| Attribute | Type | Meaning |
|---|---|---|
| `hermes.tool.cpu.utilization.avg` | float 0..1 | Mean process-tree CPU during the tool window |
| `hermes.tool.cpu.utilization.peak` | float 0..1 | Highest sample in the window |
| `hermes.tool.gpu.utilization.avg` | float 0..1 | Mean host GPU busy ratio during the window (only when a GPU was probed) |
| `hermes.tool.gpu.utilization.peak` | float 0..1 | Highest GPU sample in the window |

CPU is attributable to the tool (and whatever it spawned). GPU is the
**host's coincident load** during the window, not attribution: a tool that
runs while another tenant uses the GPU will show that load. Tools shorter than
the sampling interval omit the attributes rather than report a guess.

Together with [`hermes.turn.number`](/reference/span-attributes),
this lets a backend answer "which turn, which tool, and what was the machine
doing at the time" from a single span.

## Reading it

Grafana / LGTM (Prometheus naming appends the unit and swaps dots for
underscores):

```promql
process_cpu_utilization{cpu_mode="user"}
hw_gpu_utilization{hw_id="gpu0"}
hw_power_watts{hw_type="gpu"}
```

Phoenix ingests traces only, so it shows the per-tool span attributes but not
the gauges; pair it with a metrics-capable backend (LGTM, OpenObserve, SigNoz)
to see both.

## Alternatives

If you would rather not sample inside the Hermes process, the OpenTelemetry
Collector's [`hostmetrics` receiver](https://github.com/open-telemetry/opentelemetry-collector-contrib/tree/main/receiver/hostmetricsreceiver)
emits the same `process.*` / `system.*` metrics, and NVIDIA's
[DCGM exporter](https://github.com/NVIDIA/dcgm-exporter) covers GPUs in far
more detail. With `host_metrics` on the plugin sets the `host.name` resource
attribute (override it under `resource_attributes`), which joins those series
to its traces. What the Collector cannot do is stamp the per-tool window on the
span — that part needs to be in-process.
