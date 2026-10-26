#!/bin/sh
# Render a launcher page to a PNG with headless Edge (demo data, no game or save touched).
# usage: tools/ui_shot.sh <page> <out.png> [width height] [theme] [lang]
PAGE=${1:-home}; OUT=${2:-shot.png}; W=${3:-1360}; H=${4:-900}
EDGE="/c/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"
ROOT="$(cd "$(dirname "$0")/.." && pwd -W 2>/dev/null || pwd)"
"$EDGE" --headless=new --disable-gpu --hide-scrollbars --window-size=$W,$H --virtual-time-budget=5000 \
  --lang=${6:-en-US} --screenshot="$OUT" "file:///$ROOT/apps/launcher/ui/index.html?shot&theme=${5:-day}${7:+&$7}#/$PAGE" >/dev/null 2>&1
