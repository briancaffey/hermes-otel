#!/usr/bin/env bash
# Every scene, in the order the cli scene relies on (see scenes/cli.yaml).
set -euo pipefail
cd "$(dirname "$0")"
for s in span-tree cli cost mcp; do echo "== $s"; ./take.sh "$s" "$@"; done
