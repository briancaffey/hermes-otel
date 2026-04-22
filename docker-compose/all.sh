#!/usr/bin/env bash
# Bring up (or tear down) every hermes-otel backend together.
#
# Each backend runs as its own docker-compose *project* so their container
# names and networks stay isolated. Only host ports must be unique; see the
# table in the plugin README for the allocation.
#
# Usage:
#   docker-compose/all.sh up        # bring everything up
#   docker-compose/all.sh down      # stop + remove containers (keeps volumes)
#   docker-compose/all.sh nuke      # stop + remove containers AND volumes
#   docker-compose/all.sh status    # ps for every project
#
# Run from the plugin root.

set -euo pipefail

cd "$(dirname "$0")/.."
ACTION="${1:-up}"

STACKS=(
  "phoenix|docker-compose/phoenix.yaml"
  "langfuse|docker-compose/langfuse.yaml"
  "jaeger|docker-compose/jaeger.yaml"
  "signoz|docker-compose/signoz/docker-compose.yaml"
  # LGTM is intentionally commented out of "up all" because it collides
  # with phoenix (port 3000) and langfuse (port 3000) and with jaeger
  # (port 4318). Bring it up standalone:
  #   docker compose -p lgtm -f docker-compose/lgtm.yaml up -d
  # "lgtm|docker-compose/lgtm.yaml"
)

case "$ACTION" in
  up)
    for stack in "${STACKS[@]}"; do
      project="${stack%%|*}"
      file="${stack##*|}"
      echo "==> up: $project ($file)"
      docker compose -p "$project" -f "$file" up -d
    done
    echo
    echo "==> status"
    "$0" status
    ;;
  down)
    for stack in "${STACKS[@]}"; do
      project="${stack%%|*}"
      file="${stack##*|}"
      echo "==> down: $project"
      docker compose -p "$project" -f "$file" down
    done
    ;;
  nuke)
    for stack in "${STACKS[@]}"; do
      project="${stack%%|*}"
      file="${stack##*|}"
      echo "==> nuke: $project"
      docker compose -p "$project" -f "$file" down -v
    done
    ;;
  status)
    for stack in "${STACKS[@]}"; do
      project="${stack%%|*}"
      file="${stack##*|}"
      echo "--- $project ---"
      docker compose -p "$project" -f "$file" ps
    done
    ;;
  *)
    echo "usage: $0 {up|down|nuke|status}" >&2
    exit 2
    ;;
esac
