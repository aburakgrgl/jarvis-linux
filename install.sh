#!/usr/bin/env bash
# Jarvis installer for Arch-based distros (CachyOS, EndeavourOS, Manjaro, Arch)
set -e
cd "$(dirname "$0")"

echo "==> Installing system packages..."
sudo pacman -S --needed --noconfirm python nodejs npm alsa-utils libnotify

echo "==> Installing Claude Code (if missing)..."
command -v claude >/dev/null 2>&1 || sudo npm install -g @anthropic-ai/claude-code

echo "==> Creating Python virtual environment..."
python -m venv .venv
source .venv/bin/activate
pip install --upgrade pip

echo "==> Installing Python packages..."
# openwakeword is installed without deps: its tflite-runtime dependency
# does not support modern Python. We use the ONNX backend instead.
pip install --no-deps openwakeword
pip install -r requirements.txt

echo "==> Downloading fallback voice (English, alan)..."
mkdir -p voices
cd voices
python -m piper.download_voices en_GB-alan-medium
cd ..

echo ""
echo "================================================================"
echo "Done! Next steps:"
echo "  1. Run 'claude' and log in with 'Claude account with subscription'"
echo "  2. Make sure ANTHROPIC_API_KEY env var is NOT set"
echo "  3. Test:  source .venv/bin/activate.fish && python jarvis.py"
echo "     (bash/zsh users: source .venv/bin/activate)"
echo "  4. Optional: install as a service -> see README"
echo "================================================================"
