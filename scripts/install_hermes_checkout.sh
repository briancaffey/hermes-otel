#!/usr/bin/env bash
# Install a hermes-agent checkout into the `python` on PATH, the way its own
# lock resolves it, plus any extra `uv pip install` arguments:
#
#   scripts/install_hermes_checkout.sh "$RUNNER_TEMP/hermes-agent" -r hermes_otel/requirements.txt
#
# hermes-agent overrides some pins through uv's `[tool.uv]
# override-dependencies` (Pillow 13 over browser-harness's exact
# `pillow==12.3.0`, for one). pip does not read that table, so since
# 2026-10 a plain `pip install -e` of hermes-agent main ends in
# ResolutionImpossible (#637). The same list goes to uv as --overrides.
set -euo pipefail

checkout=$1
shift
overrides=$(mktemp)
python - "$checkout/pyproject.toml" >"$overrides" <<'EOF'
import sys
import tomllib

with open(sys.argv[1], "rb") as f:
    uv = tomllib.load(f).get("tool", {}).get("uv", {})
print("\n".join(uv.get("override-dependencies", [])))
EOF
uv pip install --python "$(command -v python)" --quiet --overrides "$overrides" -e "$checkout" "$@"
