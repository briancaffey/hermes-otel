#!/usr/bin/env bash
# One take, end to end: record the scene, edit the cast, render the MP4 (+ a still at 70 %).
#   tools/take.sh span-tree [render.py options…]
set -euo pipefail
cd "$(dirname "$0")/.."
name=$1; shift || true
# Hermes autosuggests from the profile's prompt history; a prompt typed in an earlier take would show
# up as dim ghost text ahead of the cursor. Every take starts with an empty history.
: > "${HERMES_ROOT:-$HOME/.hermes}/profiles/${DEMO_PROFILE:-promo}/.hermes_history"
tools/record.py "scenes/$name.yaml"
tools/edit_cast.py "casts/$name.raw.cast"
dur=$(python3 -c "import json,sys;l=open('casts/$name.cast').read().splitlines()[1:];print(sum(json.loads(x)[0] for x in l if x.strip()))")
tools/render.py "casts/$name.cast" --still "$(python3 -c "print(round($dur*0.7,1))")" --still "$(python3 -c "print(round($dur,1))")" "$@"
