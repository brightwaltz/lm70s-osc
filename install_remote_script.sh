#!/bin/bash
# LM70S_OSC リモートスクリプトを Ableton の User Library にコピーする
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)/remote_script/LM70S_OSC"
DEST="${ABLETON_USER_LIBRARY:-$HOME/Music/Ableton/User Library}/Remote Scripts"

mkdir -p "$DEST"
rm -rf "$DEST/LM70S_OSC"
rsync -a --exclude __pycache__ "$SRC" "$DEST/"
echo "installed: $DEST/LM70S_OSC"
echo "Live を再起動し、Settings > Link, Tempo & MIDI の Control Surface で LM70S_OSC を選んでください。"
