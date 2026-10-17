"""Generate the "Agents: Hermes and Claude Code" Grafana dashboard.

    uv run python scripts/grafana/agents_dashboard.py
    -> docker-compose/lgtm/grafana-dashboards/agents-hermes-claude-code.json

The dashboard reads only the ``claude_code.*`` metric contract that Claude
Code's own telemetry and hermes-otel's ``metric_name_compat`` both emit, plus
the ``service_name`` label the LGTM Prometheus promotes from the resource, so
Hermes and Claude Code sit on the same axes. It is a companion to the community
dashboard this repo vendors beside it, "Claude Code Metrics (Prometheus)"
(grafana.com 25255, Rock Martel-Langlois, MIT), whose cache-hit-ratio query it
adapts; everything else is written for the two-agent comparison.

Design rules (the dataviz method; every palette below was run through its
validator for the dark Grafana surface):

* Color follows the entity, never the rank. Hermes is always blue, Claude Code
  always orange, in every panel, whatever the filter leaves on screen. A model's
  bar wears its agent's hue.
* Token types are one fixed categorical set (input, output, cacheRead,
  cacheCreation) in a validated order, never cycled.
* Magnitude is a bar; a headline is a stat tile; change over time is a bucketed
  bar (spend *in* each interval), not an extrapolated per-hour rate. No pies, no
  dual axes, no status colors on a ratio.
* Nothing Hermes cannot fill: no commits, lines of code, users or terminals.
* Totals are ``increase()`` over the dashboard range. A series exported once by a
  short-lived process has an increase only when Prometheus ingests a zero sample
  at its start (the LGTM stack runs with ``created-timestamp-zero-ingestion``).
"""

from __future__ import annotations

import json
from pathlib import Path

OUT = (
    Path(__file__).resolve().parents[2]
    / "docker-compose"
    / "lgtm"
    / "grafana-dashboards"
    / "agents-hermes-claude-code.json"
)

DS = {"type": "prometheus", "uid": "prometheus"}
EXPR = {"type": "__expr__", "uid": "__expr__"}

# Validated for the dark surface (OKLab, adjacent pairs, >= 3:1 contrast).
HERMES = "#3987e5"  # categorical slot 1, blue
CLAUDE = "#d95926"  # categorical slot 2, orange
TOKEN_TYPES = (  # fixed order, validated as a set
    ("input", "#199e70"),
    ("output", "#9085e9"),
    ("cacheRead", "#c98500"),
    ("cacheCreation", "#d55181"),
)
AGENT_NAMES = {"hermes-agent": "Hermes", "claude-code": "Claude Code"}

SEL = 'service_name=~"$agent", model=~"$model"'  # token + cost series carry model
SEL_AGENT = 'service_name=~"$agent"'  # session + active_time do not
COST = f"claude_code_cost_usage_USD_total{{{SEL}}}"
TOK = f"claude_code_token_usage_tokens_total{{{SEL}}}"
SESS = f"claude_code_session_count_total{{{SEL_AGENT}}}"
ACTIVE = f"claude_code_active_time_seconds_total{{{SEL_AGENT}}}"


CACHE_READ = 'type="cacheRead"'
CACHE_DENOM = 'type=~"cacheRead|cacheCreation|input"'


def nice_agent(expr: str) -> str:
    """Short agent names in the series label (`Hermes`, `Claude`), so a narrow
    bar panel never truncates them; the colour overrides match the same words."""
    return (
        "label_replace(label_replace("
        + expr
        + ', "service_name", "Claude", "service_name", "claude-code"), '
        '"service_name", "Hermes", "service_name", "hermes-agent")'
    )


def short_model(expr: str) -> str:
    """`openai/gpt-4o-mini` -> `gpt-4o-mini`: the vendor prefix is the agent's
    colour already, and the full name is in the tooltip."""
    return "label_replace(" + expr + ', "model", "$1", "model", ".*/(.*)")'


def _tok(extra: str) -> str:
    return f"claude_code_token_usage_tokens_total{{{SEL}, {extra}}}"


CACHE_HIT_RANGE = (
    f"sum(increase({_tok(CACHE_READ)}[$__range])) / "
    f"sum(increase({_tok(CACHE_DENOM)}[$__range]))"
)

_id = 0


def nid() -> int:
    global _id
    _id += 1
    return _id


def target(expr: str, legend: str = "", ref: str = "A", instant: bool = True, hide=False):
    t = {
        "datasource": DS,
        "refId": ref,
        "expr": expr,
        "legendFormat": legend or "__auto",
        "instant": instant,
        "range": not instant,
        "editorMode": "code",
    }
    if hide:
        t["hide"] = True
    return t


def agent_overrides(prop: str = "color") -> list:
    """Hermes blue / Claude Code orange wherever a series is named by agent or model."""
    return [
        {
            "matcher": {"id": "byRegexp", "options": "/(claude|Claude)/"},
            "properties": [{"id": prop, "value": {"mode": "fixed", "fixedColor": CLAUDE}}],
        },
    ]


def rename_agents() -> list:
    return [
        {
            "matcher": {"id": "byName", "options": raw},
            "properties": [{"id": "displayName", "value": nice}],
        }
        for raw, nice in AGENT_NAMES.items()
    ]


def stat(title, targets, unit, x, y, w=4, h=4, decimals=None, description=""):
    defaults = {
        "unit": unit,
        "color": {"mode": "fixed", "fixedColor": "text"},
        "thresholds": {"mode": "absolute", "steps": [{"color": "text", "value": None}]},
    }
    if decimals is not None:
        defaults["decimals"] = decimals
    return {
        "id": nid(),
        "type": "stat",
        "title": title,
        "description": description,
        "datasource": DS,
        "gridPos": {"x": x, "y": y, "w": w, "h": h},
        "targets": targets,
        "fieldConfig": {"defaults": defaults, "overrides": []},
        "options": {
            "colorMode": "none",
            "graphMode": "none",
            "justifyMode": "center",
            "orientation": "auto",
            "textMode": "value",
            "wideLayout": True,
            "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
        },
    }


def bars(
    title,
    expr,
    legend,
    unit,
    x,
    y,
    w,
    h,
    overrides,
    description="",
    decimals=None,
    sort_desc=True,
    max_items=None,
    names="left",
):
    """A horizontal bar panel: one thin bar per series, direct value labels."""
    defaults = {
        "unit": unit,
        "min": 0,
        "color": {"mode": "fixed", "fixedColor": HERMES},
        "thresholds": {"mode": "absolute", "steps": [{"color": HERMES, "value": None}]},
    }
    if decimals is not None:
        defaults["decimals"] = decimals
    if max_items:
        expr = f"topk({max_items}, {expr})"
    if sort_desc:
        expr = f"sort_desc({expr})"
    return {
        "id": nid(),
        "type": "bargauge",
        "title": title,
        "description": description,
        "datasource": DS,
        "gridPos": {"x": x, "y": y, "w": w, "h": h},
        "targets": [target(expr, legend)],
        "fieldConfig": {"defaults": defaults, "overrides": overrides},
        "options": {
            "displayMode": "basic",
            "orientation": "horizontal",
            "namePlacement": names,
            "showUnfilled": True,
            # Thin marks and readable names: a fixed 16 px bar, 13/15 px text,
            # instead of Grafana's auto-sizing that fills the panel with the
            # bar and truncates the series name.
            "sizing": "manual",
            "minVizHeight": 16,
            "maxVizHeight": 16,
            "minVizWidth": 8,
            "valueMode": "text",
            "text": {"titleSize": 13, "valueSize": 15},
            "legend": {"showLegend": False, "displayMode": "list", "placement": "bottom"},
            "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
        },
    }


def table(title, keys, columns, x, y, w, h, description="", sort_by=None, limit=None):
    """One row per entity, one inline bar per measure.

    ``keys``: [(label, column title)], the group-by labels shown as text.
    ``columns``: [(title, expr, unit, decimals)], each an instant query
    summed by the key labels; the frames are merged on the keys. Bars are
    one hue (magnitude, not identity: the row label carries identity), each
    column scaled to its own maximum.
    """
    targets, overrides, rename, index = [], [], {}, {}
    for i, (label, col) in enumerate(keys):
        rename[label] = col
        index[label] = i
        if label == "service_name":
            # the agent's display name, as text (identity never rides on colour alone)
            overrides.append(
                {
                    "matcher": {"id": "byName", "options": col},
                    "properties": [
                        {"id": "custom.width", "value": 150},
                        {
                            "id": "mappings",
                            "value": [
                                {
                                    "type": "value",
                                    "options": {
                                        raw: {"text": nice, "index": j}
                                        for j, (raw, nice) in enumerate(AGENT_NAMES.items())
                                    },
                                }
                            ],
                        },
                    ],
                }
            )
    for i, (col, expr, unit, decimals) in enumerate(columns):
        ref = chr(ord("A") + i)
        t = target(expr, ref=ref)
        t["format"] = "table"
        t["legendFormat"] = ""
        targets.append(t)
        value = f"Value #{ref}"
        rename[value] = col
        index[value] = len(keys) + i
        props = [
            {"id": "unit", "value": unit},
            {"id": "min", "value": 0},
            {
                "id": "custom.cellOptions",
                "value": {"type": "gauge", "mode": "basic", "valueDisplayMode": "text"},
            },
            {"id": "custom.width", "value": 150},
        ]
        if decimals is not None:
            props.append({"id": "decimals", "value": decimals})
        overrides.append({"matcher": {"id": "byName", "options": col}, "properties": props})
    transformations = [
        {"id": "organize", "options": {"excludeByName": {"Time": True}}},
        {"id": "merge", "options": {}},
        {
            "id": "organize",
            "options": {"excludeByName": {}, "indexByName": index, "renameByName": rename},
        },
    ]
    if sort_by:
        transformations.append(
            {"id": "sortBy", "options": {"sort": [{"field": sort_by, "desc": True}]}}
        )
    if limit:
        transformations.append({"id": "limit", "options": {"limitField": limit}})
    return {
        "id": nid(),
        "type": "table",
        "title": title,
        "description": description,
        "datasource": DS,
        "gridPos": {"x": x, "y": y, "w": w, "h": h},
        "targets": targets,
        "transformations": transformations,
        "fieldConfig": {
            "defaults": {
                "custom": {
                    "align": "auto",
                    "cellOptions": {"type": "auto"},
                    "inspect": False,
                    "filterable": False,
                },
                "color": {"mode": "fixed", "fixedColor": HERMES},
                "thresholds": {"mode": "absolute", "steps": [{"color": HERMES, "value": None}]},
            },
            "overrides": overrides,
        },
        "options": {
            "showHeader": True,
            "cellHeight": "sm",
            "footer": {"show": False, "reducer": ["sum"], "countRows": False, "fields": ""},
        },
    }


def series(
    title,
    targets,
    unit,
    x,
    y,
    w,
    h,
    overrides,
    description="",
    stacked=True,
    draw="bars",
    fill=70,
    percent=False,
):
    custom = {
        "drawStyle": draw,
        "lineWidth": 2 if draw == "line" else 1,
        "fillOpacity": fill if draw == "bars" else 12,
        "gradientMode": "none",
        "barAlignment": 0,
        "barWidthFactor": 0.8,
        "lineInterpolation": "linear",
        "showPoints": "auto",  # a lone bucket (one Claude Code run) still shows as a point
        "pointSize": 8,
        "spanNulls": False,
        "stacking": {"mode": "normal" if stacked else "none", "group": "A"},
        "axisPlacement": "auto",
        "axisBorderShow": False,
        "axisColorMode": "text",
        "axisSoftMin": 0,
        "scaleDistribution": {"type": "linear"},
        "thresholdsStyle": {"mode": "off"},
        "hideFrom": {"legend": False, "tooltip": False, "viz": False},
    }
    defaults = {
        "unit": unit,
        "min": 0,
        "custom": custom,
        "color": {"mode": "fixed", "fixedColor": HERMES},
        "thresholds": {"mode": "absolute", "steps": [{"color": "text", "value": None}]},
    }
    if percent:
        defaults["max"] = 1
    return {
        "id": nid(),
        "type": "timeseries",
        "title": title,
        "description": description,
        "datasource": DS,
        "interval": "5m",
        "gridPos": {"x": x, "y": y, "w": w, "h": h},
        "targets": targets,
        "fieldConfig": {"defaults": defaults, "overrides": overrides},
        "options": {
            "legend": {
                "showLegend": True,
                "displayMode": "list",
                "placement": "bottom",
                "calcs": [],
            },
            "tooltip": {"mode": "multi", "sort": "desc"},
        },
    }


def row(title, y):
    return {
        "id": nid(),
        "type": "row",
        "title": title,
        "collapsed": False,
        "gridPos": {"x": 0, "y": y, "w": 24, "h": 1},
        "panels": [],
    }


def text(y):
    md = (
        "Hermes and Claude Code on one set of axes, read from the four metrics both "
        "export under Claude Code's names: `claude_code.session.count`, "
        "`claude_code.token.usage`, `claude_code.cost.usage`, `claude_code.active_time.total` "
        "(Hermes: `metric_name_compat: claude_code` in hermes-otel). **Hermes is blue, Claude "
        "Code is orange, in every panel.** Totals are `increase()` over the selected range; "
        "the time panels show the amount *in* each 5-minute bucket, not a per-hour rate. "
        "Companion to the community dashboard "
        "[Claude Code Metrics (Prometheus), grafana.com 25255](https://grafana.com/grafana/dashboards/25255-claude-code-metrics-prometheus/) "
        "by Rock Martel-Langlois (MIT), vendored next to this one; its cache-hit-ratio query "
        "is adapted here. Built by [hermes-otel](https://github.com/briancaffey/hermes-otel) "
        "(`scripts/grafana/agents_dashboard.py`)."
    )
    return {
        "id": nid(),
        "type": "text",
        "title": "",
        "transparent": True,
        "gridPos": {"x": 0, "y": y, "w": 24, "h": 3},
        "options": {"mode": "markdown", "content": md},
    }


def build() -> dict:
    panels = []
    y = 0
    panels.append(text(y))
    y += 3

    # ── Overview: six headline numbers ───────────────────────────────────
    panels.append(row("Overview", y))
    y += 1
    panels += [
        stat(
            "Sessions",
            [target(f"sum(increase({SESS}[$__range]))")],
            "short",
            0,
            y,
            decimals=0,
            description="Session starts in the range (both agents). "
            "`increase(claude_code_session_count_total)`.",
        ),
        stat(
            "Spend",
            [target(f"sum(increase({COST}[$__range]))")],
            "currencyUSD",
            4,
            y,
            description="Each agent's own price for its API calls. Hermes: `agent.usage_pricing`; "
            "Claude Code: its client-side estimate.",
        ),
        stat(
            "Tokens",
            [target(f"sum(increase({TOK}[$__range]))")],
            "short",
            8,
            y,
            decimals=0,
            description="input + output + cacheRead + cacheCreation.",
        ),
        stat(
            "Active time",
            [target(f"sum(increase({ACTIVE}[$__range]))")],
            "s",
            12,
            y,
            description="Seconds the agents were working (`type=cli`): a Hermes turn's wall-clock, "
            "Claude Code's tool and response time. Claude Code's `user` typing time is excluded "
            "so the two compare.",
        ),
        stat(
            "Spend per session",
            [
                target(f"sum(increase({COST}[$__range]))", ref="A", hide=True),
                target(f"sum(increase({SESS}[$__range]))", ref="B", hide=True),
                {"datasource": EXPR, "refId": "C", "type": "math", "expression": "$A / $B"},
            ],
            "currencyUSD",
            16,
            y,
            description="Spend divided by session starts.",
        ),
        stat(
            "Cache hit ratio",
            [target(CACHE_HIT_RANGE)],
            "percentunit",
            20,
            y,
            decimals=1,
            description="cacheRead / (cacheRead + cacheCreation + input), the same definition "
            "for both agents (query adapted from grafana.com 25255).",
        ),
    ]
    y += 4

    # ── Side by side: one table, agents as rows ──────────────────────────
    panels.append(row("Hermes and Claude Code, side by side", y))
    y += 1
    by_agent = "sum by (service_name)"
    panels.append(
        table(
            "By agent",
            [("service_name", "Agent")],
            [
                ("Sessions", f"{by_agent} (increase({SESS}[$__range]))", "short", 0),
                ("Spend", f"{by_agent} (increase({COST}[$__range]))", "currencyUSD", None),
                ("Tokens", f"{by_agent} (increase({TOK}[$__range]))", "short", 0),
                ("Active time", f"{by_agent} (increase({ACTIVE}[$__range]))", "s", None),
                (
                    "Spend per session",
                    f"{by_agent} (increase({COST}[$__range])) / {by_agent} (increase({SESS}[$__range]))",
                    "currencyUSD",
                    None,
                ),
                (
                    "Tokens per session",
                    f"{by_agent} (increase({TOK}[$__range])) / {by_agent} (increase({SESS}[$__range]))",
                    "short",
                    0,
                ),
                (
                    "Cache hit",
                    f"{by_agent} (increase({_tok(CACHE_READ)}[$__range])) / "
                    f"{by_agent} (increase({_tok(CACHE_DENOM)}[$__range]))",
                    "percentunit",
                    1,
                ),
            ],
            0,
            y,
            24,
            5,
            sort_by="Spend",
            description="Each bar is scaled to its column's largest value. Cache hit = "
            "cacheRead / (cacheRead + cacheCreation + input).",
        )
    )
    y += 5

    # ── Over time ────────────────────────────────────────────────────────
    panels.append(row("Over time, per 5-minute bucket", y))
    y += 1
    ov = agent_overrides() + rename_agents()
    tok_ov = [
        {
            "matcher": {"id": "byName", "options": name},
            "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": hexv}}],
        }
        for name, hexv in TOKEN_TYPES
    ]
    panels += [
        series(
            "Spend by agent",
            [
                target(
                    f"sum by (service_name) (increase({COST}[$__interval]))",
                    "{{service_name}}",
                    instant=False,
                )
            ],
            "currencyUSD",
            0,
            y,
            12,
            8,
            ov,
        ),
        series(
            "Tokens by type",
            [target(f"sum by (type) (increase({TOK}[$__interval]))", "{{type}}", instant=False)],
            "short",
            12,
            y,
            12,
            8,
            tok_ov,
            description="Stacked: input (uncached prompt), output, cacheRead, cacheCreation.",
        ),
    ]
    y += 8
    panels += [
        series(
            "Active time by agent",
            [
                target(
                    f"sum by (service_name) (increase({ACTIVE}[$__interval]))",
                    "{{service_name}}",
                    instant=False,
                )
            ],
            "s",
            0,
            y,
            12,
            7,
            ov,
        ),
        series(
            "Cache hit ratio by agent",
            [
                target(
                    f"sum by (service_name) (increase({_tok(CACHE_READ)}[$__interval])) / "
                    f"sum by (service_name) (increase({_tok(CACHE_DENOM)}[$__interval]))",
                    "{{service_name}}",
                    instant=False,
                )
            ],
            "percentunit",
            12,
            y,
            12,
            7,
            ov,
            stacked=False,
            draw="line",
            percent=True,
            description="Per bucket; a bucket with no prompt tokens leaves a gap.",
        ),
    ]
    y += 7

    # ── Models: one table, the agent as a column ──────────────────────────
    panels.append(row("Models", y))
    y += 1
    by_model = "sum by (service_name, model)"
    panels.append(
        table(
            "By model",
            [("service_name", "Agent"), ("model", "Model")],
            [
                ("Spend", f"{by_model} (increase({COST}[$__range]))", "currencyUSD", None),
                ("Tokens", f"{by_model} (increase({TOK}[$__range]))", "short", 0),
                (
                    "Cache hit",
                    f"{by_model} (increase({_tok(CACHE_READ)}[$__range])) / "
                    f"{by_model} (increase({_tok(CACHE_DENOM)}[$__range]))",
                    "percentunit",
                    1,
                ),
            ],
            0,
            y,
            24,
            7,
            sort_by="Spend",
        )
    )
    y += 7

    # ── Sessions and query source ────────────────────────────────────────
    panels.append(row("Sessions and query source", y))
    y += 1
    by_sess = "sum by (service_name, session_id)"
    panels.append(
        table(
            "Most expensive sessions",
            [("service_name", "Agent"), ("session_id", "Session")],
            [
                ("Spend", f"{by_sess} (increase({COST}[$__range]))", "currencyUSD", None),
                ("Tokens", f"{by_sess} (increase({TOK}[$__range]))", "short", 0),
            ],
            0,
            y,
            14,
            9,
            sort_by="Spend",
            limit=12,
            description="Needs `session.id` on the metrics: Claude Code sends it by default, "
            "Hermes with `metric_name_compat_session_id: true`. Without it an agent is one row.",
        )
    )
    by_qs = "sum by (service_name, query_source)"
    panels.append(
        table(
            "By query source",
            [("service_name", "Agent"), ("query_source", "Query source")],
            [
                ("Spend", f"{by_qs} (increase({COST}[$__range]))", "currencyUSD", None),
                ("Tokens", f"{by_qs} (increase({TOK}[$__range]))", "short", 0),
            ],
            14,
            y,
            10,
            9,
            sort_by="Spend",
            description="`main` is the agent loop, `subagent` a delegated child, `auxiliary` "
            "the calls made on the agent's behalf (titles, compaction, ...). The same label "
            "and values in both tools.",
        )
    )
    y += 9

    return {
        "uid": "hermes-otel-agents",
        "title": "Agents: Hermes and Claude Code",
        "description": (
            "Hermes and Claude Code compared on the claude_code.* metric contract. "
            "Companion to grafana.com 25255 (Claude Code Metrics (Prometheus), Rock "
            "Martel-Langlois, MIT). Generated by scripts/grafana/agents_dashboard.py in hermes-otel."
        ),
        "tags": ["hermes-otel", "claude-code", "agents"],
        "editable": True,
        "graphTooltip": 1,
        "timezone": "browser",
        "schemaVersion": 42,
        "version": 1,
        "refresh": "30s",
        "time": {"from": "now-3h", "to": "now"},
        "timepicker": {},
        "links": [
            {
                "title": "Claude Code Metrics (Prometheus), grafana.com 25255",
                "type": "link",
                "icon": "external link",
                "targetBlank": True,
                "url": "https://grafana.com/grafana/dashboards/25255-claude-code-metrics-prometheus/",
            },
            {
                "title": "hermes-otel guide",
                "type": "link",
                "icon": "doc",
                "targetBlank": True,
                "url": "https://briancaffey.github.io/hermes-otel/guides/claude-code-dashboards",
            },
        ],
        "templating": {
            "list": [
                {
                    "name": "agent",
                    "label": "Agent",
                    "type": "query",
                    "datasource": DS,
                    "definition": "label_values(claude_code_session_count_total, service_name)",
                    "query": {
                        "query": "label_values(claude_code_session_count_total, service_name)",
                        "refId": "agent",
                    },
                    "includeAll": True,
                    "multi": True,
                    "allValue": ".*",
                    "current": {"text": ["All"], "value": ["$__all"]},
                    "refresh": 2,
                    "sort": 1,
                    "options": [],
                },
                {
                    "name": "model",
                    "label": "Model",
                    "type": "query",
                    "datasource": DS,
                    "definition": 'label_values(claude_code_token_usage_tokens_total{service_name=~"$agent"}, model)',
                    "query": {
                        "query": 'label_values(claude_code_token_usage_tokens_total{service_name=~"$agent"}, model)',
                        "refId": "model",
                    },
                    "includeAll": True,
                    "multi": True,
                    "allValue": ".*",
                    "current": {"text": ["All"], "value": ["$__all"]},
                    "refresh": 2,
                    "sort": 1,
                    "options": [],
                },
            ]
        },
        "annotations": {"list": []},
        "panels": panels,
    }


if __name__ == "__main__":
    OUT.write_text(json.dumps(build(), indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUT.relative_to(Path.cwd()) if OUT.is_relative_to(Path.cwd()) else OUT}")
