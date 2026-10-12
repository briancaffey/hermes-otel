#!/usr/bin/env bash
# Install the pinned terminal-recording tools from their GitHub releases into
# ~/.local/bin, as the current user, without Homebrew. Safe to re-run. See #437.
set -euo pipefail

ASCIINEMA_VERSION=v3.2.1
ASCIINEMA_SHA256_ARM64=1f0c76da7855601df93e5dccdf69b7c683b81beff1411e38b3802de1f5fc7a1c
AGG_VERSION=v1.9.0
AGG_SHA256_ARM64=742b2b6230529b72f310acb835e9479496000f2eabc97b0993cabe1d7fe70171

BIN_DIR="${BIN_DIR:-$HOME/.local/bin}"

case "$(uname -s)-$(uname -m)" in
  Darwin-arm64) triple=aarch64-apple-darwin ;;
  *) echo "only Darwin arm64 is pinned here; add the triple and its sha256" >&2; exit 1 ;;
esac

install_one() {
  local repo=$1 tag=$2 name=$3 sha=$4
  local url="https://github.com/$repo/releases/download/$tag/$name-$triple"
  local tmp
  tmp=$(mktemp -d)
  curl -fsSL -o "$tmp/$name" "$url"
  local got
  got=$(shasum -a 256 "$tmp/$name" | cut -d' ' -f1)
  if [ "$got" != "$sha" ]; then
    echo "$name: sha256 $got does not match the pinned $sha" >&2
    exit 1
  fi
  chmod +x "$tmp/$name"
  xattr -d com.apple.quarantine "$tmp/$name" 2>/dev/null || true
  mkdir -p "$BIN_DIR"
  mv "$tmp/$name" "$BIN_DIR/$name"
  rmdir "$tmp"
  echo "$BIN_DIR/$name: $("$BIN_DIR/$name" --version)"
}

install_one asciinema/asciinema "$ASCIINEMA_VERSION" asciinema "$ASCIINEMA_SHA256_ARM64"
install_one asciinema/agg "$AGG_VERSION" agg "$AGG_SHA256_ARM64"
