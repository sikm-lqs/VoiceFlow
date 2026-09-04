# VoiceFlow

[中文文档](README.md)

**Local Voice Input Assistant** — Hold F10 (smart paste) or F11 (terminal paste) to speak, release to transcribe and paste at the cursor.

No cloud, no app switching. Just speak and the text appears.

---

## Features

- **Dual hotkey mode** — F10 smart paste (auto-detects window type), F11 terminal paste (forces `ctrl+shift+v`)
- **Auto-paste** — Recognized text is inserted directly at the cursor position
- **Smart window detection** — Automatically detects terminal windows and selects the correct paste shortcut
- **Fully local** — Audio never leaves the machine, privacy-first
- **Low latency** — End-to-end in 1-3 seconds
- **Swappable backend** — ASR model is configurable
- **Lightweight daemon** — Near-zero CPU when idle, < 50MB memory

## Quick Start

### 1. Install system dependencies

```bash
sudo apt install xclip xdotool libportaudio2
```

### 2. Start ASR service

A local vLLM service is required (e.g. qwen3-asr):

```bash
pip install "vllm[audio]"
python -m vllm.entrypoints.openai.api_server \
  --model /path/to/qwen3-asr-1.7B \
  --host 127.0.0.1 --port 8000
```

### 3. Install and run

```bash
cd voiceflow-ui
pnpm install
pnpm tauri dev
```

Production runs the packaged `/usr/bin/voiceflow` binary with the Python sidecar and configuration in `/opt/voiceflow/`.

Hold **F10** (smart paste) or **F11** (terminal paste) to start recording, release to transcribe and paste.

## Architecture

The production application has two processes:

```text
systemd user service
  -> Tauri/Rust application (/usr/bin/voiceflow)
     -> Python sidecar (/opt/voiceflow/voiceflow_py/daemon.py)
        -> local vLLM ASR HTTP API
```

The Rust process owns global hotkeys, the overlay window, target-window focus, clipboard handling, and paste injection. The Python sidecar owns microphone capture, temporary WAV files, ASR requests, and terminal-window detection. They communicate with newline-delimited JSON over stdin/stdout.

## Usage

```
Normal apps: Focus input field → Hold F10 → Speak → Release F10 → Text appears at cursor
Terminals:   Focus terminal → Hold F10 (auto-detected) or F11 (force terminal mode) → Speak → Release → Text pasted
```

## Configuration

Edit `config.yaml` to customize:

```yaml
hotkey:
  smart_combo: f10       # Smart paste mode (auto-detect window type)
  terminal_combo: f11    # Terminal paste mode (forces ctrl+shift+v)

asr:
  endpoint: http://127.0.0.1:8000
  model: /path/to/qwen3-asr-1.7B
  language: zh

paste:
  smart_mode: true                    # Enable smart window detection
  default_shortcut: ctrl+v            # Paste shortcut for normal windows
  terminal_shortcut: ctrl+shift+v     # Paste shortcut for terminals
  restore_clipboard: true
  terminal_classes: [...]             # List of terminal window class names
  terminal_title_keywords: [...]      # List of terminal title keywords

ux:
  indicator: true
  indicator_follow_pointer: true
  start_beep: true
  notify: true
```

## Tech Stack

| Component | Technology |
|-----------|------------|
| Application | Tauri/Rust + React |
| Sidecar | Python 3.12 |
| Hotkey | Tauri global shortcut (X11) |
| Recording | Python sounddevice |
| ASR backend | vLLM (OpenAI-compatible API) |
| Clipboard | xclip + xdotool |
| Testing | pytest + requests-mock |

## Requirements

- **OS**: Ubuntu Desktop (X11)
- **Python**: >= 3.10
- **GPU**: Required for ASR model inference (e.g. RTX 4070+)
- **ASR Model**: A speech recognition model served by vLLM

## License

MIT
