#!/usr/bin/env bash
# Fetch the render-time assets into marketing/terminal/.cache (gitignored): the asciinema web
# player and JetBrains Mono (OFL), both pinned. See #441.
set -euo pipefail
PLAYER_VERSION=3.17.0
JBM_VERSION=2.304
C="$(cd "$(dirname "$0")/.." && pwd)/.cache"
mkdir -p "$C/fonts"
for f in asciinema-player.min.js asciinema-player.css; do
  curl -fsSL -o "$C/$f" "https://cdn.jsdelivr.net/npm/asciinema-player@$PLAYER_VERSION/dist/bundle/$f"
done
tmp=$(mktemp -d)
curl -fsSL -o "$tmp/jbm.zip" "https://github.com/JetBrains/JetBrainsMono/releases/download/v$JBM_VERSION/JetBrainsMono-$JBM_VERSION.zip"
for s in Regular Bold Italic BoldItalic; do
  unzip -q -o -j "$tmp/jbm.zip" "fonts/ttf/JetBrainsMono-$s.ttf" -d "$C/fonts"
done
rm -rf "$tmp"
echo "assets in $C"
