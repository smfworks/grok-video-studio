#!/usr/bin/env bash
# Grok Video Studio installer: copies scripts/config into $VSTUDIO_HOME and installs Piper TTS at pinned, hash-checked URLs.
# Usage: ./install.sh [VSTUDIO_HOME]   (default /workspace/vstudio)
set -euo pipefail
HOME_DIR="${1:-/workspace/vstudio}"
SRC="$(cd "$(dirname "$0")" && pwd)"
PIPER_URL="https://github.com/rhasspy/piper/releases/download/2023.11.14-2/piper_linux_x86_64.tar.gz"
PIPER_SHA="a50cb45f355b7af1f6d758c1b360717877ba0a398cc8cbe6d2a7a3a26e225992"
VOICE_BASE="https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/ljspeech/high"
VOICE_ONNX_SHA="5d4f08ba6a2a48c44592eed3ce56bf85e9de3dd4e20df90541ae68a8310c029a"
VOICE_JSON_SHA="7e1f4634af596d83cca997fb7a931ba80b70f8a316a2655ee69c55365e0ace14"

[ "$(uname -m)" = "x86_64" ] || { echo "Piper pinned build is x86_64 only" >&2; exit 1; }
for t in ffmpeg ffprobe fc-match python3 curl sha256sum; do command -v "$t" >/dev/null || { echo "missing $t" >&2; exit 1; }; done

fetch() { # url dest sha
  curl -fsSL --retry 2 -o "$2.part" "$1"
  echo "$3  $2.part" | sha256sum -c --quiet - || { rm -f "$2.part"; echo "sha256 mismatch for $1" >&2; exit 1; }
  mv "$2.part" "$2"
}

mkdir -p "$HOME_DIR/bin" "$HOME_DIR/config" "$HOME_DIR/piper/voices"
cp "$SRC"/scripts/*.py "$HOME_DIR/bin/"; chmod +x "$HOME_DIR"/bin/*.py
cp -n "$SRC"/config/*.json "$HOME_DIR/config/" 2>/dev/null || true
cp "$SRC/config/pack.schema.json" "$HOME_DIR/config/pack.schema.json"

P="$HOME_DIR/piper"
if [ ! -x "$P/bin/piper" ]; then
  fetch "$PIPER_URL" "$P/piper_linux_x86_64.tar.gz" "$PIPER_SHA"
  tmp="$(mktemp -d)"; tar xzf "$P/piper_linux_x86_64.tar.gz" -C "$tmp"; rm -rf "$P/bin"; mv "$tmp/piper" "$P/bin"; rmdir "$tmp"
fi
[ -f "$P/voices/en_US-ljspeech-high.onnx" ] || fetch "$VOICE_BASE/en_US-ljspeech-high.onnx" "$P/voices/en_US-ljspeech-high.onnx" "$VOICE_ONNX_SHA"
[ -f "$P/voices/en_US-ljspeech-high.onnx.json" ] || fetch "$VOICE_BASE/en_US-ljspeech-high.onnx.json" "$P/voices/en_US-ljspeech-high.onnx.json" "$VOICE_JSON_SHA"

python3 -c "import numpy, librosa, faster_whisper" 2>/dev/null || python3 -m pip install --quiet -r "$SRC/requirements.txt" \
  || python3 -m pip install --quiet --break-system-packages -r "$SRC/requirements.txt"
echo "installed to $HOME_DIR"
