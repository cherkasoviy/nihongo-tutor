#!/usr/bin/env bash
# Download the KanjiVG stroke-order SVGs for kana into the Mini App's static assets.
#
# KanjiVG is CC BY-SA 3.0 (Ulrich Apel). It is NOT vendored in this repository: it carries its own
# licence and attribution obligations, and the kana grid is complete without it — the StrokeOrder
# component simply renders nothing when a file is missing.
#
# Usage: make fetch-kanjivg
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DEST="$ROOT/miniapp/public/kanjivg"
RELEASE="${KANJIVG_RELEASE:-r20240807}"
URL="https://github.com/KanjiVG/kanjivg/releases/download/$RELEASE/kanjivg-$RELEASE-main.zip"

command -v curl >/dev/null || { echo "curl is required" >&2; exit 1; }
command -v unzip >/dev/null || { echo "unzip is required" >&2; exit 1; }

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

echo "Downloading KanjiVG $RELEASE…"
curl -fsSL "$URL" -o "$tmp/kanjivg.zip"
unzip -q "$tmp/kanjivg.zip" -d "$tmp"

mkdir -p "$DEST"
count=0
# Hiragana U+3041–U+309F, katakana U+30A0–U+30FF. The component asks for 5-digit lowercase hex.
for f in "$tmp"/kanji/*.svg; do
  name="$(basename "$f")"
  code="${name%%-*}"; code="${code%.svg}"
  case "$code" in
    030[4-9]?|0309?|030a?|030b?|030c?|030d?|030e?|030f?)
      # Skip variant files (e.g. 03042-Kaisho.svg): the base glyph only.
      [[ "$name" == *-* ]] && continue
      cp "$f" "$DEST/$code.svg"; count=$((count + 1)) ;;
  esac
done

echo "Wrote $count kana stroke-order files to miniapp/public/kanjivg"
echo "KanjiVG is CC BY-SA 3.0 — keep the attribution in the Mini App."
