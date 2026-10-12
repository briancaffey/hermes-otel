#!/usr/bin/env bash
# Join rendered clips into one review reel with short fades between them:
#   tools/reel.sh span-tree cli cost mcp      # -> renders/reel.mp4
set -euo pipefail
cd "$(dirname "$0")/.."
fade=0.4
inputs=(); filter=""; offset=0; prev="0:v"
for i in "$@"; do inputs+=(-i "renders/$i.mp4"); done
n=$#
for ((k = 1; k < n; k++)); do
  dur=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "renders/${!k}.mp4")
  offset=$(python3 -c "print(round($offset + $dur - $fade, 3))")
  out="v$k"
  filter+="[$prev][$k:v]xfade=transition=fade:duration=$fade:offset=$offset[$out];"
  prev=$out
done
if [ "$n" -gt 1 ]; then
  ffmpeg -loglevel error -y "${inputs[@]}" -filter_complex "${filter%;}" -map "[$prev]" \
    -c:v libx264 -preset slow -crf 16 -pix_fmt yuv420p -movflags +faststart renders/reel.mp4
else
  cp "renders/$1.mp4" renders/reel.mp4
fi
echo "renders/reel.mp4 ($(ffprobe -v error -show_entries format=duration -of csv=p=0 renders/reel.mp4) s)"
