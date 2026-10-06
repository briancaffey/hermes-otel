#!/usr/bin/env bash
# Bring up, tear down or inspect the hermes-otel backend stacks.
#
# Every subfolder of docker-compose/ is one backend: <name>/docker-compose.yaml
# plus its config files and a README. Compose names the project after the
# folder, so no -p flag is needed.
#
# Usage (from the plugin root):
#   docker-compose/all.sh up <name>...      # e.g. up phoenix openobserve
#   docker-compose/all.sh down <name>...    # stop + remove containers (keeps volumes)
#   docker-compose/all.sh nuke <name>...    # stop + remove containers AND volumes
#   docker-compose/all.sh status [<name>...]
#   docker-compose/all.sh list              # every stack name
#
# Without names, `up` starts the LIGHT set (phoenix, jaeger, openobserve), and
# `down`/`nuke`/`status` act on every stack. Nothing starts all stacks at once:
# the heavy ones (langfuse, signoz, uptrace, opik, langwatch, laminar,
# langtrace, latitude) need gigabytes each and several share host ports — see
# the port map in README.md.

set -euo pipefail

cd "$(dirname "$0")/.."
ACTION="${1:-status}"
shift || true

LIGHT=(phoenix jaeger openobserve)
ALL=()
for d in docker-compose/*/; do
  [ -f "$d/docker-compose.yaml" ] && ALL+=("$(basename "$d")")
done

if [ "$#" -gt 0 ]; then
  STACKS=("$@")
elif [ "$ACTION" = up ]; then
  STACKS=("${LIGHT[@]}")
else
  STACKS=("${ALL[@]}")
fi

compose() {
  local name="$1"; shift
  local file="docker-compose/$name/docker-compose.yaml"
  [ -f "$file" ] || { echo "no such stack: $name (see: $0 list)" >&2; exit 2; }
  docker compose -f "$file" "$@"
}

case "$ACTION" in
  up)
    for s in "${STACKS[@]}"; do
      echo "==> up: $s"
      # maple builds its image from the GitHub release tarball
      if [ "$s" = maple ]; then compose "$s" up -d --build; else compose "$s" up -d; fi
    done
    ;;
  down)
    for s in "${STACKS[@]}"; do echo "==> down: $s"; compose "$s" down --remove-orphans; done
    ;;
  nuke)
    for s in "${STACKS[@]}"; do echo "==> nuke: $s"; compose "$s" down -v --remove-orphans; done
    ;;
  status)
    for s in "${STACKS[@]}"; do
      out="$(compose "$s" ps --format '{{.Name}} {{.Status}}' 2>/dev/null || true)"
      if [ -n "$out" ]; then echo "--- $s ---"; echo "$out"; fi
    done
    ;;
  list)
    printf '%s\n' "${ALL[@]}"
    echo "(ports, logins and plugin snippets: docker-compose/README.md and docker-compose/<name>/README.md)"
    ;;
  *)
    echo "usage: $0 {up|down|nuke|status|list} [stack...]" >&2
    exit 2
    ;;
esac
