"""The backend manifest (#402): one record per backend type.

Every per-type fact the plugin used to keep in a separate table lives here:
what the exporter side needs (display name, which OTLP signals the backend
accepts, temporality and root-usage presets, how env-var mode selects it,
which credentials each resolver falls back to), what the Settings tab shows
(UI rule, the env-var inventory), what the dashboard needs (adapter module,
aliases, the dashboard-only yaml keys an adapter reads, its default query
port) and where the documented exceptions are. ``backends.py``,
``settings_report.py``, ``mcp_config.py`` and the dashboard registry derive
their tables from :data:`SPECS`; ``scripts/gen_config_docs.py`` renders the
type list and the backend env-var reference from it.

Adding a backend: one :class:`BackendSpec` here, a ``_resolve_<type>`` in
``backends.py`` (registered in ``_RESOLVERS``), optionally an adapter module
under ``dashboard/backends/`` named in ``adapter_module``, and a docs page.
``tests/unit/test_backend_specs.py`` checks the pieces agree.

No OTel imports here: the dashboard package and the scripts import this.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Tuple

#: The metric temporality a type's docs ask for; ``None`` is the SDK default.
Temporality = Optional[str]


@dataclass(frozen=True)
class CredentialField:
    """A ``BackendConfig`` credential field and the env vars its resolver falls
    back to when neither the inline value nor ``<field>_env`` is set."""

    field: str
    env: Tuple[str, ...]


@dataclass(frozen=True)
class AdapterSignals:
    """What the dashboard adapter can *read*, when that differs from what the
    exporter *sends* (``signal_support``): a documented exception."""

    metrics: bool
    logs: bool
    reason: str


@dataclass(frozen=True)
class BackendSpec:
    type: str
    display_name: str
    #: Dashboard-only spellings of the type (``hyperdx`` for ``clickstack``).
    aliases: Tuple[str, ...] = ()
    #: Which OTLP signals the backend accepts by default. Every type takes
    #: traces; an explicit ``traces`` / ``metrics`` / ``logs`` on the entry wins.
    metrics: bool = True
    logs: bool = False
    temporality: Temporality = None
    #: ``False`` for backends that sum ``gen_ai.usage.*`` over every span (#327).
    root_usage: bool = True
    #: Plugin-namespaced variables, one of which must be set for env-var mode
    #: to select this type (#255). Empty: the endpoint variable alone selects.
    env_opt_in: Tuple[str, ...] = ()
    #: The vendor SDK variables that used to select the type on their own;
    #: each inner tuple is one alternative spelling (#259).
    vendor_credentials: Tuple[Tuple[str, ...], ...] = ()
    credentials: Tuple[CredentialField, ...] = ()
    #: ``OTEL_<TYPE>_ENDPOINT`` when the resolver reads one.
    endpoint_env: Optional[str] = None
    #: The port the read API / UI listens on in the repo's compose stacks.
    default_query_port: Optional[int] = None
    #: Where the web UI lives relative to the OTLP endpoint (settings card):
    #: ``origin`` | ``proxied`` | ``fixed`` | ``None``.
    ui_rule: Optional[str] = None
    #: Module under ``hermes_otel.dashboard.backends`` whose adapter handles
    #: this type; ``None`` = export-only (``status`` lists it as not queryable).
    adapter_module: Optional[str] = None
    adapter_signals: Optional[AdapterSignals] = None
    #: Dashboard-only yaml keys the adapter reads; kept on ``BackendConfig.extra``.
    query_keys: Tuple[str, ...] = ()
    #: ``(name, description)`` for the Settings tab's env-var inventory.
    env_vars: Tuple[Tuple[str, str], ...] = ()
    #: The resolver decides the signals itself (Victoria: a signal is on only
    #: when its store is named); ``resolve()`` then leaves them alone.
    signals_from_resolver: bool = False

    @property
    def traces_only(self) -> bool:
        return not self.metrics and not self.logs

    @property
    def docs_path(self) -> str:
        return f"/backends/{self.type}"


_SPECS = [
    BackendSpec(
        type="phoenix",
        display_name="Phoenix",
        metrics=False,
        logs=False,
        endpoint_env="OTEL_PHOENIX_ENDPOINT",
        default_query_port=6006,
        ui_rule="origin",
        adapter_module="phoenix",
        query_keys=("project", "project_name", "query_port"),
        env_vars=(("OTEL_PHOENIX_ENDPOINT", "Single-backend mode: Phoenix OTLP traces URL"),),
    ),
    BackendSpec(
        type="langfuse",
        display_name="Langfuse",
        metrics=False,
        logs=False,
        env_opt_in=(
            "OTEL_LANGFUSE_PUBLIC_API_KEY",
            "OTEL_LANGFUSE_SECRET_API_KEY",
            "OTEL_LANGFUSE_ENDPOINT",
        ),
        vendor_credentials=(("LANGFUSE_PUBLIC_KEY",), ("LANGFUSE_SECRET_KEY",)),
        credentials=(
            CredentialField("public_key", ("OTEL_LANGFUSE_PUBLIC_API_KEY", "LANGFUSE_PUBLIC_KEY")),
            CredentialField("secret_key", ("OTEL_LANGFUSE_SECRET_API_KEY", "LANGFUSE_SECRET_KEY")),
        ),
        endpoint_env="OTEL_LANGFUSE_ENDPOINT",
        default_query_port=3000,
        ui_rule="origin",
        adapter_module="langfuse",
        query_keys=("query_api", "query_port"),
        env_vars=(
            ("OTEL_LANGFUSE_ENDPOINT", "Single-backend mode: Langfuse OTLP traces URL"),
            ("LANGFUSE_BASE_URL", "Langfuse base URL (alternative to the endpoint)"),
            ("LANGFUSE_PUBLIC_KEY", "Langfuse public key fallback"),
            ("LANGFUSE_SECRET_KEY", "Langfuse secret key fallback"),
            ("OTEL_LANGFUSE_PUBLIC_API_KEY", "Langfuse public key (preferred name)"),
            ("OTEL_LANGFUSE_SECRET_API_KEY", "Langfuse secret key (preferred name)"),
        ),
    ),
    BackendSpec(
        type="signoz",
        display_name="SigNoz",
        metrics=True,
        logs=True,
        temporality="delta",
        credentials=(CredentialField("ingestion_key", ("OTEL_SIGNOZ_INGESTION_KEY",)),),
        endpoint_env="OTEL_SIGNOZ_ENDPOINT",
        default_query_port=3301,
        ui_rule="proxied",
        adapter_module="signoz",
        query_keys=("query_port", "ui_url"),
        env_vars=(
            ("OTEL_SIGNOZ_ENDPOINT", "Single-backend mode: SigNoz OTLP URL"),
            ("OTEL_SIGNOZ_INGESTION_KEY", "SigNoz Cloud ingestion key"),
        ),
    ),
    BackendSpec(
        type="jaeger",
        display_name="Jaeger",
        metrics=False,
        logs=False,
        endpoint_env="OTEL_JAEGER_ENDPOINT",
        default_query_port=16686,
        ui_rule="proxied",
        adapter_module="jaeger",
        query_keys=("query_api", "query_path", "query_port", "search_fetch"),
        env_vars=(("OTEL_JAEGER_ENDPOINT", "Single-backend mode: Jaeger OTLP URL"),),
    ),
    BackendSpec(
        type="zipkin",
        display_name="Zipkin",
        metrics=False,
        logs=False,
        endpoint_env="OTEL_ZIPKIN_ENDPOINT",
        default_query_port=9411,
        ui_rule="origin",
        adapter_module="zipkin",
        query_keys=("query_port",),
        env_vars=(
            ("OTEL_ZIPKIN_ENDPOINT", "Single-backend mode: Zipkin OTLP URL (zipkin-otel image)"),
        ),
    ),
    BackendSpec(
        type="greptime",
        display_name="GreptimeDB",
        metrics=True,
        logs=True,
        credentials=(
            CredentialField("user", ("OTEL_GREPTIME_USER",)),
            CredentialField("password", ("OTEL_GREPTIME_PASSWORD",)),
        ),
        endpoint_env="OTEL_GREPTIME_ENDPOINT",
        default_query_port=4000,
        ui_rule="origin",
        adapter_module="greptime",
        query_keys=("db", "logs_table", "metrics_match"),
        env_vars=(
            (
                "OTEL_GREPTIME_ENDPOINT",
                "Single-backend mode: GreptimeDB HTTP origin (OTLP under /v1/otlp)",
            ),
            (
                "OTEL_GREPTIME_DB",
                "GreptimeDB database for all three signals (X-Greptime-DB-Name; default public)",
            ),
            ("OTEL_GREPTIME_USER", "GreptimeDB user for Basic auth (only with a user provider)"),
            ("OTEL_GREPTIME_PASSWORD", "GreptimeDB password for Basic auth"),
        ),
    ),
    BackendSpec(
        type="clickstack",
        display_name="ClickStack",
        aliases=("hyperdx",),
        metrics=True,
        logs=True,
        env_opt_in=("OTEL_CLICKSTACK_ENDPOINT",),
        vendor_credentials=(("HYPERDX_API_KEY",),),
        credentials=(CredentialField("api_key", ("OTEL_CLICKSTACK_API_KEY", "HYPERDX_API_KEY")),),
        endpoint_env="OTEL_CLICKSTACK_ENDPOINT",
        default_query_port=8123,
        ui_rule="proxied",
        adapter_module="clickstack",
        query_keys=(
            "database",
            "logs_table",
            "metrics_match",
            "query_port",
            "traces_table",
            "ui_url",
        ),
        env_vars=(
            (
                "OTEL_CLICKSTACK_ENDPOINT",
                "Single-backend mode: ClickStack Collector OTLP/HTTP base",
            ),
            (
                "OTEL_CLICKSTACK_API_KEY",
                "ClickStack ingestion API key (bare authorization header; preferred name)",
            ),
            (
                "HYPERDX_API_KEY",
                "ClickStack / HyperDX ingestion API key fallback (the vendor SDK variable)",
            ),
        ),
    ),
    BackendSpec(
        type="victoria",
        display_name="Victoria",
        aliases=("victoriatraces",),
        metrics=True,
        logs=True,
        credentials=(
            CredentialField("user", ("OTEL_VICTORIA_USER",)),
            CredentialField("password", ("OTEL_VICTORIA_PASSWORD",)),
        ),
        endpoint_env="OTEL_VICTORIA_ENDPOINT",
        default_query_port=10428,
        ui_rule="origin",
        adapter_module="victoria",
        adapter_signals=AdapterSignals(
            metrics=False,
            logs=False,
            reason="the adapter serves metrics / logs only when prometheus_url / logs_url name the sibling stores",
        ),
        query_keys=("logs_url", "metrics_match", "prometheus_url"),
        env_vars=(
            (
                "OTEL_VICTORIA_ENDPOINT",
                "Single-backend mode: VictoriaTraces origin (OTLP at /insert/opentelemetry/v1/traces)",
            ),
            (
                "OTEL_VICTORIA_METRICS_ENDPOINT",
                "VictoriaMetrics origin (OTLP at /opentelemetry/v1/metrics); metrics off without it",
            ),
            (
                "OTEL_VICTORIA_LOGS_ENDPOINT",
                "VictoriaLogs origin (OTLP at /insert/opentelemetry/v1/logs); logs off without it",
            ),
            (
                "OTEL_VICTORIA_USER",
                "Basic auth user for the Victoria stores (only with -httpAuth.*)",
            ),
            ("OTEL_VICTORIA_PASSWORD", "Basic auth password for the Victoria stores"),
        ),
        signals_from_resolver=True,
    ),
    BackendSpec(
        type="aspire",
        display_name="Aspire Dashboard",
        metrics=True,
        logs=True,
        credentials=(CredentialField("api_key", ("OTEL_ASPIRE_API_KEY",)),),
        endpoint_env="OTEL_ASPIRE_ENDPOINT",
        default_query_port=18888,
        ui_rule="proxied",
        adapter_module="aspire",
        adapter_signals=AdapterSignals(
            metrics=False,
            logs=True,
            reason="the dashboard cannot query Aspire's metrics; OTLP metrics are still exported",
        ),
        query_keys=("logs_fetch", "query_port", "search_fetch", "ui_url"),
        env_vars=(
            ("OTEL_ASPIRE_ENDPOINT", "Single-backend mode: Aspire Dashboard OTLP/HTTP base"),
            (
                "OTEL_ASPIRE_API_KEY",
                "Aspire Dashboard OTLP API key (x-otlp-api-key); only in ApiKey auth mode",
            ),
        ),
    ),
    BackendSpec(
        type="gigapipe",
        display_name="Gigapipe",
        aliases=("qryn",),
        metrics=True,
        logs=True,
        credentials=(
            CredentialField("user", ("OTEL_GIGAPIPE_USER",)),
            CredentialField("password", ("OTEL_GIGAPIPE_PASSWORD",)),
        ),
        endpoint_env="OTEL_GIGAPIPE_ENDPOINT",
        default_query_port=3100,
        ui_rule="origin",
        adapter_module="gigapipe",
        query_keys=("query_port", "ui_url"),
        env_vars=(
            ("OTEL_GIGAPIPE_ENDPOINT", "Single-backend mode: Gigapipe (qryn) OTLP/HTTP base"),
            (
                "OTEL_GIGAPIPE_USER",
                "Basic auth user for Gigapipe (only with GIGAPIPE_LOGIN/PASSWORD)",
            ),
            ("OTEL_GIGAPIPE_PASSWORD", "Basic auth password for Gigapipe"),
        ),
    ),
    BackendSpec(
        type="agenta",
        display_name="Agenta",
        metrics=False,
        logs=False,
        env_opt_in=("OTEL_AGENTA_API_KEY", "OTEL_AGENTA_ENDPOINT"),
        vendor_credentials=(("AGENTA_API_KEY",),),
        credentials=(CredentialField("api_key", ("OTEL_AGENTA_API_KEY", "AGENTA_API_KEY")),),
        endpoint_env="OTEL_AGENTA_ENDPOINT",
        ui_rule="origin",
        adapter_module="agenta",
        query_keys=("region", "ui_url"),
        env_vars=(
            ("OTEL_AGENTA_ENDPOINT", "Agenta origin for a self-hosted stack (cloud by default)"),
            (
                "OTEL_AGENTA_API_KEY",
                "Agenta project API key (Authorization: ApiKey; preferred name)",
            ),
            ("AGENTA_API_KEY", "Agenta project API key fallback (the SDK variable)"),
            ("AGENTA_HOST", "Agenta origin fallback (the SDK variable)"),
        ),
    ),
    BackendSpec(
        type="tempo",
        display_name="Tempo",
        metrics=False,
        logs=False,
        endpoint_env="OTEL_TEMPO_ENDPOINT",
        default_query_port=3200,
        ui_rule="proxied",
        adapter_module="tempo",
        adapter_signals=AdapterSignals(
            metrics=True,
            logs=True,
            reason="the Tempo adapter reads metrics and logs through the LGTM stack's Prometheus and Loki",
        ),
        query_keys=(
            "grafana_datasource_uid",
            "loki_selector",
            "loki_url",
            "metrics_match",
            "prometheus_url",
            "query_port",
            "search_fetch",
            "ui_url",
        ),
        env_vars=(("OTEL_TEMPO_ENDPOINT", "Single-backend mode: Tempo / LGTM OTLP URL"),),
    ),
    BackendSpec(
        type="uptrace",
        display_name="Uptrace",
        metrics=True,
        logs=True,
        temporality="delta",
        credentials=(CredentialField("dsn", ("OTEL_UPTRACE_DSN", "UPTRACE_DSN")),),
        endpoint_env="OTEL_UPTRACE_ENDPOINT",
        default_query_port=14318,
        ui_rule="origin",
        adapter_module="uptrace",
        query_keys=("project_id", "service_name", "user_token", "user_token_env"),
        env_vars=(
            ("OTEL_UPTRACE_ENDPOINT", "Single-backend mode: Uptrace OTLP URL"),
            ("OTEL_UPTRACE_DSN", "Uptrace DSN (preferred name)"),
            ("UPTRACE_DSN", "Uptrace DSN fallback"),
            ("UPTRACE_USER_TOKEN", "Uptrace user token fallback for the dashboard's query API"),
        ),
    ),
    BackendSpec(
        type="openobserve",
        display_name="OpenObserve",
        aliases=("openobserver",),
        metrics=True,
        logs=True,
        credentials=(
            CredentialField("user", ("OTEL_OPENOBSERVE_USER", "OPENOBSERVE_USER")),
            CredentialField("password", ("OTEL_OPENOBSERVE_PASSWORD", "OPENOBSERVE_PASSWORD")),
        ),
        endpoint_env="OTEL_OPENOBSERVE_ENDPOINT",
        default_query_port=5080,
        ui_rule="origin",
        adapter_module="openobserve",
        query_keys=("logs_stream", "org", "query_port", "stream", "trace_window_days", "ui_url"),
        env_vars=(
            ("OTEL_OPENOBSERVE_ENDPOINT", "Single-backend mode: OpenObserve OTLP URL"),
            ("OTEL_OPENOBSERVE_STREAM", "OpenObserve stream name (default `default`)"),
            ("OTEL_OPENOBSERVE_USER", "OpenObserve user (preferred name)"),
            ("OTEL_OPENOBSERVE_PASSWORD", "OpenObserve password (preferred name)"),
            ("OPENOBSERVE_USER", "OpenObserve user fallback"),
            ("OPENOBSERVE_PASSWORD", "OpenObserve password fallback"),
        ),
    ),
    BackendSpec(
        type="parseable",
        display_name="Parseable",
        metrics=True,
        logs=True,
        credentials=(CredentialField("api_key", ("OTEL_PARSEABLE_API_KEY", "PARSEABLE_API_KEY")),),
        endpoint_env="OTEL_PARSEABLE_ENDPOINT",
        ui_rule="origin",
        env_vars=(
            ("OTEL_PARSEABLE_ENDPOINT", "Single-backend mode: Parseable OTLP URL"),
            ("OTEL_PARSEABLE_API_KEY", "Parseable API key (preferred name)"),
            ("PARSEABLE_API_KEY", "Parseable API key fallback"),
            ("PARSEABLE_TRACES_DATASET", "Parseable dataset for traces (default `hermes-traces`)"),
            (
                "PARSEABLE_METRICS_DATASET",
                "Parseable dataset for metrics (default `hermes-metrics`)",
            ),
            ("PARSEABLE_LOGS_DATASET", "Parseable dataset for logs (default `hermes-logs`)"),
        ),
    ),
    BackendSpec(
        type="honeycomb",
        display_name="Honeycomb",
        metrics=True,
        logs=True,
        env_opt_in=("OTEL_HONEYCOMB_API_KEY", "OTEL_HONEYCOMB_ENDPOINT"),
        vendor_credentials=(("HONEYCOMB_API_KEY",),),
        credentials=(CredentialField("api_key", ("OTEL_HONEYCOMB_API_KEY", "HONEYCOMB_API_KEY")),),
        endpoint_env="OTEL_HONEYCOMB_ENDPOINT",
        ui_rule="fixed",
        env_vars=(
            ("OTEL_HONEYCOMB_ENDPOINT", "Single-backend mode: Honeycomb OTLP URL"),
            ("OTEL_HONEYCOMB_API_KEY", "Honeycomb API key (preferred name)"),
            ("HONEYCOMB_API_KEY", "Honeycomb API key fallback"),
        ),
    ),
    BackendSpec(
        type="elastic",
        display_name="Elastic",
        metrics=True,
        logs=True,
        temporality="delta",
        env_opt_in=("OTEL_ELASTIC_ENDPOINT",),
        vendor_credentials=(("ELASTIC_API_KEY",),),
        credentials=(CredentialField("api_key", ("OTEL_ELASTIC_API_KEY", "ELASTIC_API_KEY")),),
        endpoint_env="OTEL_ELASTIC_ENDPOINT",
        ui_rule=None,
        env_vars=(
            (
                "OTEL_ELASTIC_ENDPOINT",
                "Single-backend mode: Elastic OTLP URL (mOTLP or EDOT Collector)",
            ),
            ("OTEL_ELASTIC_API_KEY", "Elastic API key (preferred name; needs the endpoint too)"),
            ("ELASTIC_API_KEY", "Elastic API key fallback"),
        ),
    ),
    BackendSpec(
        type="openlit",
        display_name="OpenLIT",
        metrics=True,
        logs=True,
        env_opt_in=("OTEL_OPENLIT_ENDPOINT",),
        vendor_credentials=(("OPENLIT_API_KEY",),),
        credentials=(CredentialField("api_key", ("OTEL_OPENLIT_API_KEY", "OPENLIT_API_KEY")),),
        endpoint_env="OTEL_OPENLIT_ENDPOINT",
        ui_rule="proxied",
        env_vars=(
            ("OTEL_OPENLIT_ENDPOINT", "Single-backend mode: OpenLIT OTLP/HTTP URL"),
            ("OTEL_OPENLIT_API_KEY", "OpenLIT API key (preferred name; needs the endpoint too)"),
            ("OPENLIT_API_KEY", "OpenLIT API key fallback"),
        ),
    ),
    BackendSpec(
        type="mlflow",
        display_name="MLflow",
        metrics=False,
        logs=False,
        env_opt_in=("OTEL_MLFLOW_ENDPOINT",),
        vendor_credentials=(("MLFLOW_TRACKING_TOKEN",),),
        credentials=(
            CredentialField("api_key", ("OTEL_MLFLOW_API_KEY", "MLFLOW_TRACKING_TOKEN")),
            CredentialField("experiment_id", ("OTEL_MLFLOW_EXPERIMENT_ID", "MLFLOW_EXPERIMENT_ID")),
        ),
        endpoint_env="OTEL_MLFLOW_ENDPOINT",
        ui_rule="origin",
        env_vars=(
            ("OTEL_MLFLOW_ENDPOINT", "Single-backend mode: MLflow tracking server URL"),
            (
                "OTEL_MLFLOW_EXPERIMENT_ID",
                "MLflow experiment id for the x-mlflow-experiment-id header (preferred name)",
            ),
            ("MLFLOW_EXPERIMENT_ID", "MLflow experiment id fallback"),
            (
                "OTEL_MLFLOW_API_KEY",
                "MLflow tracking token (preferred name; needs the endpoint too)",
            ),
            ("MLFLOW_TRACKING_TOKEN", "MLflow tracking token fallback"),
        ),
    ),
    BackendSpec(
        type="opik",
        display_name="Comet Opik",
        metrics=False,
        logs=False,
        root_usage=False,
        env_opt_in=("OTEL_OPIK_API_KEY", "OTEL_OPIK_ENDPOINT"),
        vendor_credentials=(("OPIK_API_KEY",),),
        credentials=(
            CredentialField("api_key", ("OTEL_OPIK_API_KEY", "OPIK_API_KEY")),
            CredentialField("workspace", ("OPIK_WORKSPACE",)),
            CredentialField("project", ("OPIK_PROJECT_NAME",)),
        ),
        endpoint_env="OTEL_OPIK_ENDPOINT",
        ui_rule="origin",
        env_vars=(
            ("OTEL_OPIK_ENDPOINT", "Single-backend mode: Opik base URL (default: Comet cloud)"),
            ("OTEL_OPIK_API_KEY", "Opik API key (preferred name; enables Opik in env-var mode)"),
            ("OPIK_API_KEY", "Opik API key fallback"),
            ("OPIK_URL_OVERRIDE", "Opik SDK base URL fallback (…/api form accepted)"),
            ("OPIK_WORKSPACE", "Opik workspace for the Comet-Workspace header (cloud)"),
            ("OPIK_PROJECT_NAME", "Opik project for the projectName header"),
        ),
    ),
    BackendSpec(
        type="laminar",
        display_name="Laminar",
        metrics=False,
        logs=True,
        env_opt_in=("OTEL_LAMINAR_API_KEY", "OTEL_LAMINAR_ENDPOINT"),
        vendor_credentials=(("LMNR_PROJECT_API_KEY",),),
        credentials=(CredentialField("api_key", ("OTEL_LAMINAR_API_KEY", "LMNR_PROJECT_API_KEY")),),
        endpoint_env="OTEL_LAMINAR_ENDPOINT",
        ui_rule="proxied",
        env_vars=(
            (
                "OTEL_LAMINAR_ENDPOINT",
                "Single-backend mode: Laminar app-server URL (default: Laminar cloud)",
            ),
            (
                "OTEL_LAMINAR_API_KEY",
                "Laminar project API key (preferred name; enables Laminar in env-var mode)",
            ),
            ("LMNR_PROJECT_API_KEY", "Laminar project API key fallback"),
            ("LMNR_BASE_URL", "Laminar SDK base URL fallback"),
        ),
    ),
    BackendSpec(
        type="langwatch",
        display_name="LangWatch",
        metrics=True,
        logs=True,
        root_usage=False,
        env_opt_in=("OTEL_LANGWATCH_API_KEY", "OTEL_LANGWATCH_ENDPOINT"),
        vendor_credentials=(("LANGWATCH_API_KEY",),),
        credentials=(
            CredentialField("api_key", ("OTEL_LANGWATCH_API_KEY", "LANGWATCH_API_KEY")),
            CredentialField("project", ("LANGWATCH_PROJECT_ID",)),
        ),
        endpoint_env="OTEL_LANGWATCH_ENDPOINT",
        ui_rule="origin",
        env_vars=(
            (
                "OTEL_LANGWATCH_ENDPOINT",
                "Single-backend mode: LangWatch base URL (default: LangWatch cloud)",
            ),
            (
                "OTEL_LANGWATCH_API_KEY",
                "LangWatch project API key (preferred name; enables LangWatch in env-var mode)",
            ),
            ("LANGWATCH_API_KEY", "LangWatch API key fallback"),
            ("LANGWATCH_ENDPOINT", "LangWatch SDK base URL fallback"),
            (
                "LANGWATCH_PROJECT_ID",
                "LangWatch project id for the X-Project-Id header (service keys)",
            ),
        ),
    ),
    BackendSpec(
        type="latitude",
        display_name="Latitude",
        metrics=False,
        logs=False,
        env_opt_in=("OTEL_LATITUDE_API_KEY", "OTEL_LATITUDE_ENDPOINT"),
        vendor_credentials=(("LATITUDE_API_KEY",), ("LATITUDE_PROJECT",)),
        credentials=(
            CredentialField("api_key", ("OTEL_LATITUDE_API_KEY", "LATITUDE_API_KEY")),
            CredentialField("project", ("LATITUDE_PROJECT",)),
        ),
        endpoint_env="OTEL_LATITUDE_ENDPOINT",
        ui_rule="proxied",
        env_vars=(
            (
                "OTEL_LATITUDE_ENDPOINT",
                "Single-backend mode: Latitude ingest URL (default: Latitude cloud)",
            ),
            (
                "OTEL_LATITUDE_API_KEY",
                "Latitude API key (preferred name; enables Latitude in env-var mode)",
            ),
            ("LATITUDE_API_KEY", "Latitude API key fallback"),
            (
                "LATITUDE_PROJECT",
                "Latitude project slug for the mandatory X-Latitude-Project header",
            ),
            ("LATITUDE_INGEST_URL", "Latitude ingest URL fallback"),
        ),
    ),
    BackendSpec(
        type="weave",
        display_name="W&B Weave",
        metrics=False,
        logs=False,
        env_opt_in=("OTEL_WEAVE_API_KEY", "OTEL_WEAVE_ENDPOINT", "OTEL_WEAVE_BASE_URL"),
        vendor_credentials=(
            ("WANDB_API_KEY",),
            ("WANDB_ENTITY", "DEFAULT_WANDB_ENTITY"),
            ("WANDB_PROJECT", "DEFAULT_WANDB_PROJECT"),
        ),
        credentials=(
            CredentialField("api_key", ("OTEL_WEAVE_API_KEY", "WANDB_API_KEY")),
            CredentialField("entity", ("WANDB_ENTITY", "DEFAULT_WANDB_ENTITY")),
            CredentialField("project", ("WANDB_PROJECT", "DEFAULT_WANDB_PROJECT")),
        ),
        endpoint_env="OTEL_WEAVE_ENDPOINT",
        ui_rule="fixed",
        env_vars=(
            ("OTEL_WEAVE_ENDPOINT", "Single-backend mode: W&B Weave OTLP URL"),
            ("WANDB_OTLP_ENDPOINT", "Weave OTLP URL fallback"),
            ("OTEL_WEAVE_BASE_URL", "Weave base URL"),
            ("WANDB_BASE_URL", "Weave base URL fallback"),
            ("OTEL_WEAVE_API_KEY", "Weave API key (preferred name)"),
            ("WANDB_API_KEY", "Weave API key fallback"),
            ("WANDB_ENTITY", "Weave entity"),
            ("WANDB_PROJECT", "Weave project"),
            ("DEFAULT_WANDB_ENTITY", "Weave entity fallback"),
            ("DEFAULT_WANDB_PROJECT", "Weave project fallback"),
        ),
    ),
    BackendSpec(
        type="langsmith",
        display_name="LangSmith",
        metrics=False,
        logs=False,
        env_opt_in=("OTEL_LANGSMITH_API_KEY", "OTEL_LANGSMITH_ENDPOINT"),
        vendor_credentials=(("LANGSMITH_API_KEY",),),
        credentials=(
            CredentialField("api_key", ("OTEL_LANGSMITH_API_KEY", "LANGSMITH_API_KEY")),
            CredentialField("project", ("OTEL_LANGSMITH_PROJECT", "LANGSMITH_PROJECT")),
        ),
        endpoint_env="OTEL_LANGSMITH_ENDPOINT",
        ui_rule="fixed",
        env_vars=(
            ("OTEL_LANGSMITH_API_KEY", "LangSmith API key (preferred name; the env opt-in)"),
            (
                "OTEL_LANGSMITH_ENDPOINT",
                "LangSmith endpoint override (default `https://api.smith.langchain.com/otel/v1/traces`; `region: eu` picks the EU host)",
            ),
            ("OTEL_LANGSMITH_PROJECT", "LangSmith project (the `Langsmith-Project` header)"),
            ("LANGSMITH_API_KEY", "LangSmith API key fallback"),
            ("LANGSMITH_ENDPOINT", "LangSmith API base fallback (`/otel/v1/traces` is appended)"),
            ("LANGSMITH_PROJECT", "LangSmith project fallback"),
        ),
    ),
    BackendSpec(
        type="lgtm",
        display_name="LGTM",
        metrics=True,
        logs=True,
        default_query_port=3200,
        ui_rule="proxied",
        adapter_module="tempo",
        query_keys=(
            "grafana_datasource_uid",
            "loki_selector",
            "loki_url",
            "metrics_match",
            "prometheus_url",
            "query_port",
            "search_fetch",
            "ui_url",
        ),
    ),
    BackendSpec(
        type="otlp",
        display_name="OTLP",
        metrics=True,
        logs=True,
        ui_rule=None,
    ),
]

#: Every spec, in the order the Settings tab's env-var inventory lists them.
SPECS: Tuple[BackendSpec, ...] = tuple(_SPECS)

#: Canonical type -> spec.
BY_TYPE: Dict[str, BackendSpec] = {s.type: s for s in SPECS}

#: Canonical types and aliases.
KNOWN_TYPES = frozenset(BY_TYPE) | frozenset(a for s in SPECS for a in s.aliases)

#: Env-var mode tries these in order; first type whose variables are set wins.
ENV_PRIORITY: Tuple[str, ...] = (
    "langsmith",
    "langfuse",
    "signoz",
    "uptrace",
    "openobserve",
    "parseable",
    "weave",
    "honeycomb",
    "elastic",
    "openlit",
    "mlflow",
    "opik",
    "laminar",
    "langwatch",
    "latitude",
    "agenta",
    "clickstack",
    "greptime",
    "victoria",
    "aspire",
    "gigapipe",
    "jaeger",
    "zipkin",
    "tempo",
    "phoenix",
)


def spec_for(backend_type: Optional[str]) -> Optional[BackendSpec]:
    """The spec for a type or one of its aliases (case-insensitive), else ``None``."""
    t = (backend_type or "").strip().lower()
    spec = BY_TYPE.get(t)
    if spec is not None:
        return spec
    for s in SPECS:
        if t in s.aliases:
            return s
    return None


def canonical_type(backend_type: Optional[str]) -> str:
    """The canonical type behind an alias; an unknown type passes through lowercased."""
    spec = spec_for(backend_type)
    return spec.type if spec is not None else (backend_type or "").strip().lower()
