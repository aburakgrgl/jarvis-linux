# Jarvis — Local Voice Assistant for Linux, powered by Claude

A privacy-respecting "Hey Jarvis" voice assistant for Linux. Wake word detection, speech-to-text and text-to-speech all run **locally on your machine**. Only the transcribed text of your command is sent to Claude, which can actually *do things* on your computer: read, write and organize files, run commands, answer questions — and reply out loud.

Built and tested on CachyOS (Arch Linux, KDE Plasma, PipeWire, Python 3.14).

## Features

- **"Hey Jarvis" wake word** — detected locally by openWakeWord (ONNX backend), no audio ever leaves your machine before the wake word
- **Microphone kill switch** — bind a hotkey (e.g. Meta+J) to toggle listening completely on/off; when off, the mic is fully closed and your system's mic indicator turns off
- **Hotkey-only mode** — optional mode with no wake word at all: the mic opens only while you speak a command
- **Claude as the brain** — uses Claude Code / Claude Agent SDK, so the assistant has real file access and can execute tasks, not just chat
- **Local speech recognition** — faster-whisper, works offline (English by default, any Whisper language supported)
- **Cinematic voice** — ElevenLabs TTS (optional, free tier works) with automatic fallback to fully local Piper TTS when offline
- **GPU particle HUD** — a 30,000-particle holographic 3D sphere rendered with OpenGL shaders: perspective projection, depth of field, additive glow, a purple core with layered blue/teal shells, breathing motion and real-time audio reactivity — floating transparently over your desktop
- **Hallucination guard** — silence is never sent to Whisper, and known Whisper subtitle hallucinations are filtered out

## Requirements

- Arch-based Linux (CachyOS, EndeavourOS, Manjaro, Arch). Other distros work too; just install the equivalents of the packages in `install.sh` manually.
- A microphone and speakers
- A [Claude](https://claude.ai) Pro/Max subscription (used through Claude Code), or an Anthropic API key

## Install

```bash
git clone https://github.com/aburakgrgl/jarvis-linux ~/jarvis
cd ~/jarvis
./install.sh
```

Then connect Claude:

```bash
claude          # choose "Claude account with subscription", log in, then /exit
```

Make sure `ANTHROPIC_API_KEY` is **not** set in your environment, otherwise Claude Code will bill the API instead of using your subscription.

Test run:

```bash
source .venv/bin/activate        # fish: source .venv/bin/activate.fish
python jarvis.py
```

Say **"Hey Jarvis"**, wait for the beep, then speak your command.

## Run as a background service (autostart)

```bash
mkdir -p ~/.config/systemd/user
cp jarvis.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now jarvis
```

Logs: `journalctl --user -u jarvis -f`

## Mic toggle hotkey (KDE)

System Settings → Shortcuts → Add Command:

```
pkill -USR1 -f jarvis.py
```

Assign a key (e.g. **Meta+J**). Pressing it toggles listening on/off with a desktop notification. While off, the microphone is completely closed.

## Configuration

Everything lives at the top of `jarvis.py`:

| Setting | What it does |
|---|---|
| `MODE` | `"wake"` (Hey Jarvis + toggle hotkey) or `"hotkey"` (push-to-talk only) |
| `WHISPER_LANG` | Speech recognition language, e.g. `"tr"`, `"en"` |
| `PIPER_VOICE` | Path to the Piper TTS voice model |
| `SYSTEM_PROMPT` | The assistant's personality and reply language |
| `WAKE_THRESHOLD` | Wake word sensitivity (raise if it false-triggers) |
| `ENERGY_THRESHOLD` | Silence threshold (raise in noisy rooms) |
| `CLAUDE_CWD` | Root directory Claude is allowed to work in |
| `USE_CLI` | `True` = `claude -p` (subscription), `False` = Agent SDK |

Voice settings live in `voice.py`:

| Setting | What it does |
|---|---|
| `ELEVEN_VOICE_ID` | ElevenLabs voice (default: Brian). Free-tier premade voices work |
| `ELEVEN_MODEL` | `eleven_turbo_v2_5` (quality) or `eleven_flash_v2_5` (speed) |
| `VOICE_SETTINGS` | Stability/expressiveness of the voice |
| `SYSTEM_PROMPT` | JARVIS personality (English) |

To enable ElevenLabs, set `ELEVENLABS_API_KEY` in the systemd unit (see the commented line in `jarvis.service`). Without a key, Jarvis speaks with the local Piper voice.

### Changing the language / voice

1. Download a voice, e.g. American English:
   ```bash
   cd voices && python -m piper.download_voices en_US-lessac-medium
   ```
   Browse all voices: https://rhasspy.github.io/piper-samples/
2. In `jarvis.py` set `PIPER_VOICE` to the new `.onnx`, set `WHISPER_LANG = "en"`, and rewrite `SYSTEM_PROMPT` in English.

## Troubleshooting

- **`tflite-runtime` install error** — expected on Python ≥3.12; `install.sh` already works around it (`pip install --no-deps openwakeword` + ONNX backend).
- **Whisper replies with subtitle credits ("Altyazı M.K.", "thanks for watching")** — Whisper hallucinates on silence; this repo already filters it, but if you see new phrases add them to `HALLUCINATIONS` in `jarvis.py`.
- **Service crashes with `FileNotFoundError: 'piper'`** — the systemd unit must contain the `Environment=PATH=...` line (included in `jarvis.service`).
- **"Credit balance too low"** — Claude Code is logged into a Console (API) account. Run `claude`, then `/login` and pick *Claude account with subscription*.
- **HUD not staying on top (KDE Wayland)** — add a window rule for the HUD window in KDE settings.
- **HUD crashes with `'QOpenGLContext' object has no attribute 'functions'`** — install PyOpenGL (`pip install PyOpenGL`, already in requirements.txt).
- **HUD shows a black square instead of transparency (some Wayland setups)** — driver-dependent; ask in the issues with your GPU/compositor info.
- **ElevenLabs `402 Payment Required`** — that voice needs a paid plan; pick a premade voice (Brian, Daniel, George) or another free-tier-compatible voice ID.

## Privacy

The microphone stream is processed in 80 ms chunks in RAM by a local wake word model and immediately discarded. Nothing is recorded or transmitted until the wake word fires. After that, only the **text transcription** of your command is sent to Anthropic via Claude Code. The toggle hotkey closes the microphone entirely.

## License

MIT
