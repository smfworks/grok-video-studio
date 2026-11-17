#!/usr/bin/env bash
# Grok Video Studio installer: copies scripts/config/kits into $VSTUDIO_HOME and installs Piper TTS and the kit fonts at pinned, hash-checked URLs.
# Usage: ./install.sh [VSTUDIO_HOME]   (default /workspace/vstudio)
set -euo pipefail
HOME_DIR="${1:-/workspace/vstudio}"
SRC="$(cd "$(dirname "$0")" && pwd)"
PIPER_URL="https://github.com/rhasspy/piper/releases/download/2023.11.14-2/piper_linux_x86_64.tar.gz"
PIPER_SHA="a50cb45f355b7af1f6d758c1b360717877ba0a398cc8cbe6d2a7a3a26e225992"
VOICE_BASE="https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/ljspeech/high"
VOICE_ONNX_SHA="5d4f08ba6a2a48c44592eed3ce56bf85e9de3dd4e20df90541ae68a8310c029a"
VOICE_JSON_SHA="7e1f4634af596d83cca997fb7a931ba80b70f8a316a2655ee69c55365e0ace14"
# Kit fonts (SIL OFL 1.1) from google/fonts at a pinned commit
FONT_BASE="https://raw.githubusercontent.com/google/fonts/7af0d90b4d532ffa4b041937b560c6df4e7b1e95/ofl"
CAVEAT_SHA="e565bd7bffd895ca64f8d1cb77c816b9b38d9c010246386a07c9648592f6f282"
KALAM_SHA="2f6576601db015d4f6c08678120277fc8510b98c06e932ce7a6a9cbff4cbdded"
INTER_SHA="29160a80ff49ddcab2c97711247e08b1fab27a484a329ce8b813d820dc559031"

[ "$(uname -m)" = "x86_64" ] || { echo "Piper pinned build is x86_64 only" >&2; exit 1; }
for t in ffmpeg ffprobe fc-match python3 curl sha256sum; do command -v "$t" >/dev/null || { echo "missing $t" >&2; exit 1; }; done

fetch() { # url dest sha
  curl -fsSL --retry 2 -o "$2.part" "$1"
  echo "$3  $2.part" | sha256sum -c --quiet - || { rm -f "$2.part"; echo "sha256 mismatch for $1" >&2; exit 1; }
  mv "$2.part" "$2"
}

mkdir -p "$HOME_DIR/bin" "$HOME_DIR/config" "$HOME_DIR/piper/voices" "$HOME_DIR/kits" "$HOME_DIR/fonts"
cp "$SRC"/scripts/*.py "$HOME_DIR/bin/"; chmod +x "$HOME_DIR"/bin/*.py
cp "$SRC"/kits/*.py "$HOME_DIR/kits/"
cp -n "$SRC"/config/*.json "$HOME_DIR/config/" 2>/dev/null || true
cp "$SRC/config/pack.schema.json" "$HOME_DIR/config/pack.schema.json"
cp "$SRC/VERSION" "$HOME_DIR/VERSION"

P="$HOME_DIR/piper"
if [ ! -x "$P/bin/piper" ]; then
  fetch "$PIPER_URL" "$P/piper_linux_x86_64.tar.gz" "$PIPER_SHA"
  tmp="$(mktemp -d)"; tar xzf "$P/piper_linux_x86_64.tar.gz" -C "$tmp"; rm -rf "$P/bin"; mv "$tmp/piper" "$P/bin"; rmdir "$tmp"
fi
[ -f "$P/voices/en_US-ljspeech-high.onnx" ] || fetch "$VOICE_BASE/en_US-ljspeech-high.onnx" "$P/voices/en_US-ljspeech-high.onnx" "$VOICE_ONNX_SHA"
[ -f "$P/voices/en_US-ljspeech-high.onnx.json" ] || fetch "$VOICE_BASE/en_US-ljspeech-high.onnx.json" "$P/voices/en_US-ljspeech-high.onnx.json" "$VOICE_JSON_SHA"

F="$HOME_DIR/fonts"
[ -f "$F/CaveatBrush-Regular.ttf" ] || fetch "$FONT_BASE/caveatbrush/CaveatBrush-Regular.ttf" "$F/CaveatBrush-Regular.ttf" "$CAVEAT_SHA"
[ -f "$F/Kalam-Bold.ttf" ] || fetch "$FONT_BASE/kalam/Kalam-Bold.ttf" "$F/Kalam-Bold.ttf" "$KALAM_SHA"
[ -f "$F/Inter-VariableFont_opsz,wght.ttf" ] || fetch "$FONT_BASE/inter/Inter%5Bopsz,wght%5D.ttf" "$F/Inter-VariableFont_opsz,wght.ttf" "$INTER_SHA"

python3 -c "import numpy, librosa, faster_whisper, PIL, openpyxl, requests" 2>/dev/null || python3 -m pip install --quiet -r "$SRC/requirements.txt" \
  || python3 -m pip install --quiet --break-system-packages -r "$SRC/requirements.txt"
echo "installed $(cat "$SRC/VERSION") to $HOME_DIR"
