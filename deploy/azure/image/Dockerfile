# Hermes Agent with the hermes-otel plugin, for Azure Container Apps.
#
# Adds four things to the Hermes image: the two Python packages the plugin
# declares that the image lacks, the plugin at a pinned ref, its config, and a
# boot step that installs plugin and config into the data volume.
ARG HERMES_IMAGE=docker.io/nousresearch/hermes-agent:v0.23.3
FROM ghcr.io/astral-sh/uv:0.13.5 AS uv

FROM ${HERMES_IMAGE}

# A release tag (hermes-otel-v1.33.3) or a commit of briancaffey/hermes-otel.
ARG HERMES_OTEL_REF=b846e1ed6cfcc4660ce3c4b680f3a7f9aa4c6fde

USER root

# The Hermes image ships the OpenTelemetry API, SDK and OTLP/HTTP exporter, but
# not the OTLP/gRPC exporter (the Container Apps telemetry agent accepts gRPC
# only) or PyYAML. Without them Hermes builds a new Python environment for the
# plugin on first boot, on the data volume: minutes on a network share. The
# gRPC exporter is pinned to the SDK version already in the image.
COPY --from=uv /uv /usr/local/bin/uv
RUN sdk="$(/opt/hermes/.venv/bin/python -c 'import importlib.metadata as m; print(m.version("opentelemetry-sdk"))')" \
    && uv pip install --python /opt/hermes/.venv/bin/python \
        "opentelemetry-exporter-otlp-proto-grpc==${sdk}" "PyYAML>=6,<7" \
    && rm /usr/local/bin/uv

ADD https://github.com/briancaffey/hermes-otel/archive/${HERMES_OTEL_REF}.tar.gz /tmp/hermes-otel.tar.gz
RUN mkdir -p /opt/hermes-otel /tmp/hermes-otel-src \
    && tar -xzf /tmp/hermes-otel.tar.gz -C /tmp/hermes-otel-src --strip-components=1 \
    && cp -R /tmp/hermes-otel-src/hermes_otel /opt/hermes-otel/plugin \
    && rm -rf /tmp/hermes-otel.tar.gz /tmp/hermes-otel-src

# This image already holds every package the plugin declares, so the copy of
# the manifest it installs declares none. Otherwise enabling the plugin makes
# Hermes build a Python environment and a package cache for it on the data
# volume, which takes longer than ten minutes on an Azure Files share.
RUN /opt/hermes/.venv/bin/python -c "import re, pathlib; p = pathlib.Path('/opt/hermes-otel/plugin/plugin.yaml'); t = p.read_text(); n = re.sub(r'(?m)^python_dependencies:\\n(?:  - .*\\n)+', '', t); assert n != t; p.write_text(n)"

COPY hermes_otel.yaml /opt/hermes-otel/hermes_otel.yaml
COPY 03-hermes-otel /etc/cont-init.d/03-hermes-otel
RUN chmod 0755 /etc/cont-init.d/03-hermes-otel
