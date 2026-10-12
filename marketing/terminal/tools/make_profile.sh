#!/usr/bin/env bash
# Create (or refresh) the `promo` Hermes profile used for the terminal recordings (#438):
# Nemotron Super on NVIDIA, the terminal and skills toolsets only, hermes-otel from this
# checkout exporting to the local docker-compose/jaeger stack, and the otel MCP server.
# The NVIDIA key is copied from the `minimal` profile's .env and never printed.
set -euo pipefail

PROFILE=${PROFILE:-promo}
SOURCE_PROFILE=${SOURCE_PROFILE:-minimal}
HERMES_ROOT=${HERMES_ROOT:-$HOME/.hermes}
REPO=$(git -C "$(dirname "$0")" rev-parse --show-toplevel)
P="$HERMES_ROOT/profiles/$PROFILE"

if [ ! -d "$P" ]; then
  hermes profile create "$PROFILE" --no-skills --no-alias \
    --description "hermes-otel terminal demo recordings (marketing/terminal)"
fi

cat > "$P/config.yaml" <<YAML
model:
  default: nvidia/nemotron-3-super-120b-a12b
  provider: nvidia
toolsets:
  - terminal
  - skills
agent:
  disabled_toolsets: [browser, browser-use, delegation, memory, code_execution, tts, vision,
    clarify, todo, session_search, connections, web, file, image_gen]
skills:
  external_dirs: []
  template_vars: true
display:
  show_reasoning: false
  # Off: the CLI streams replies raw (literal ** and backticks); off lets it render markdown.
  streaming: false
  final_response_markdown: render
  timestamps: false
  credits_notices: false
  tool_preview_length: 64  # keeps the long otel.py path on one line
  bell_on_complete: false
auxiliary:
  # The post-turn self-improvement fork keeps the CLI busy after each answer and leaves its
  # own root spans in the store; the recordings do not need it.
  background_review:
    enabled: false
approvals:
  mode: "off"
telemetry:
  # Answers Hermes's first-run "Help improve Hermes?" offer so it never shows on camera.
  shared_metrics:
    enabled: false
    send: false
    offer_version: 2  # the "No thanks" answer to the current offer, as Hermes records it
onboarding:
  # One-time hints (busy input, tool progress, OpenClaw residue, profile build) already "seen".
  seen:
    busy_input_prompt: true
    tool_progress_prompt: true
    openclaw_residue_cleanup: true
    profile_build_offered: true
plugins:
  enabled: [hermes_otel]
  disabled: []
YAML

# The CLI streams replies raw, so markdown would show as literal ** and backticks on camera.
cat > "$P/SOUL.md" <<'SOUL'
You are Hermes Agent, built by Nous Research. Be direct and brief: a one-line question gets a one-line answer. No filler, no restating the request, no narrating tool calls the user can see.

Keep formatting light: no headings, no tables made of pipes. When you show output from a tool (a span tree, a table from otel.py, a list of traces), put it in a fenced code block exactly as the tool printed it, then add at most two short sentences about what it shows. Use bold sparingly for the one or two numbers that answer the question.
SOUL

# Only the model key; no messaging tokens, no backend credentials.
grep -E '^NVIDIA_API_KEY=' "$HERMES_ROOT/profiles/$SOURCE_PROFILE/.env" > "$P/.env"
chmod 600 "$P/.env"

cat > "$P/hermes_otel.yaml" <<YAML
project_name: hermes-promo
query_backend: jaeger-local
content_capture: "full"
force_flush_wait_ms: 3000
backends:
  - type: jaeger
    name: jaeger-local
    endpoint: http://localhost:4318/v1/traces
    query_port: 16686
YAML

mkdir -p "$P/plugins"
ln -sfn "$REPO/hermes_otel" "$P/plugins/hermes_otel"

# The otel MCP server over the same Jaeger (#304's helper prints and runs the add).
HERMES_HOME="$P" "${HERMES_PYTHON:-$HOME/git/hermes-agent/venv/bin/python}" "$REPO/hermes_otel/skills/observability/scripts/otel.py" mcp-config --apply --force </dev/null || \
  echo "mcp-config --apply failed; register it by hand with: hermes -p $PROFILE mcp add otel ..." >&2


# `hermes profile create --no-skills` still seeds autonomous-ai-agents; the demo shows only
# the plugin's observability skill.
rm -rf "$P/skills/autonomous-ai-agents"

echo "profile $PROFILE ready at $P"
