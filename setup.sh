#!/usr/bin/env bash
# One-command setup for yt-automation. Idempotent - safe to re-run.
# Usage: ./setup.sh [--no-kokoro] [--sample-clip]
set -euo pipefail

cd "$(dirname "$0")"

PY="${PYTHON:-python3}"
NO_KOKORO=0
SAMPLE_CLIP=0

for arg in "$@"; do
  case "$arg" in
    --no-kokoro)   NO_KOKORO=1 ;;
    --sample-clip) SAMPLE_CLIP=1 ;;
    --help|-h)     echo "Usage: ./setup.sh [--no-kokoro] [--sample-clip]"; exit 0 ;;
    *)             echo "Unknown option: $arg"; exit 1 ;;
  esac
done

echo "==> Creating directories"
mkdir -p assets/gameplay assets/kokoro output/audio output/videos output/thumbnails logs config

echo "==> Python virtualenv"
if [ ! -x .venv/bin/python ]; then
  "$PY" -m venv .venv
fi
.venv/bin/pip install --upgrade pip -q
.venv/bin/pip install -r requirements.txt -q
echo "    Dependencies installed."

if [ "$NO_KOKORO" = "0" ]; then
  if [ -s assets/kokoro/kokoro-v1.0.onnx ] && [ -s assets/kokoro/voices-v1.0.bin ]; then
    echo "==> Kokoro TTS models already present, skipping download"
  else
    echo "==> Downloading Kokoro TTS models (~350 MB)"
    curl -L --fail --progress-bar -o assets/kokoro/kokoro-v1.0.onnx \
      "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx"
    curl -L --fail --progress-bar -o assets/kokoro/voices-v1.0.bin \
      "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin"
    echo "    Kokoro models downloaded."
  fi
else
  echo "==> Skipping Kokoro download (--no-kokoro)"
fi

echo "==> Config"
if [ ! -f config/config.json ]; then
  cp config/config.example.json config/config.json
  echo "    Created config/config.json from config.example.json"
else
  echo "    config/config.json already exists (leaving as-is)"
fi

echo "==> Gameplay footage"
if ls assets/gameplay/*.mp4 >/dev/null 2>&1; then
  echo "    Found $(ls assets/gameplay/*.mp4 | wc -l) clip(s)"
elif [ "$SAMPLE_CLIP" = "1" ]; then
  echo "    Generating a sample gameplay clip (60s) so the pipeline is testable"
  .venv/bin/python - <<'PY'
from moviepy import ColorClip
import os
os.makedirs("assets/gameplay", exist_ok=True)
ColorClip(size=(1080, 1920), color=(32, 40, 58), duration=60).write_videofile(
    "assets/gameplay/sample.mp4", fps=30, codec="libx264",
    preset="ultrafast", audio=False, logger=None)
print("    Wrote assets/gameplay/sample.mp4")
PY
else
  echo "    NOTE: assets/gameplay/ is empty - add your own .mp4 clips"
  echo "          (or re-run with --sample-clip for a placeholder)"
fi

echo
echo "Setup complete. Next steps:"
echo "  1. YouTube upload (required for posting):"
echo "     - Put your Google OAuth client JSON at config/client_secret.json"
echo "     - Run: .venv/bin/python src/gen_auth.py   (then finish_auth.py)"
echo "  2. Optional: save your ElevenLabs API key to config/elevenlabs_key.txt"
echo "     (without it, the pipeline auto-falls back to the local Kokoro model)"
echo "  3. Dry-run a video:  .venv/bin/python src/main.py --no-upload"
echo "  4. Full run:         .venv/bin/python src/main.py"
echo "  5. Web dashboard:    ./start_dashboard.sh   ->  http://localhost:8080"
