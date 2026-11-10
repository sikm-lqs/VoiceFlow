# CLAUDE.md

This file provides guidance for working on the VoiceFlow repository.

## Project Overview

VoiceFlow is a local Ubuntu X11 voice input assistant. F10 uses smart paste
mode and F11 forces terminal paste mode. The production application is a
Tauri/Rust desktop process with a Python sidecar. The sidecar sends audio to a
separate local vLLM ASR service.

## Architecture

```text
systemd user service
  -> /usr/bin/voiceflow (Tauri/Rust + React)
     -> voiceflow_py/daemon.py (Python sidecar)
        -> POST /v1/audio/transcriptions
           -> local vLLM ASR service
```

The Rust process owns global shortcuts, the overlay window, target-window
tracking, clipboard handling, and paste injection. The Python sidecar owns
audio capture, temporary WAV files, ASR requests, and terminal-window
detection. Rust and Python communicate through newline-delimited JSON on
stdin/stdout.

Important paths:

- Tauri frontend: `voiceflow-ui/src/`
- Tauri/Rust code: `voiceflow-ui/src-tauri/src/`
- Python sidecar: `voiceflow_py/daemon.py`
- Shared sidecar modules: `audio_recorder.py`, `asr_client.py`, `window_detector.py`, `utils.py`
- Transcript cleanup: `text_cleanup.py`, optional `minimax_cleanup.py`; see `docs/CLEANUP.md`
- Runtime configuration: `config.yaml`
- Debian service template: `scripts/packaging/voiceflow.service`

Do not reintroduce the removed Python single-process entry point. Do not edit
`/opt/voiceflow` or `/usr/bin/voiceflow` as a source-of-truth change; those are
installed artifacts produced by the packaging workflow.

## Development

Install Python dependencies for sidecar modules and tests:

```bash
uv venv
uv pip install -e ".[dev]"
```

Run the Tauri application in development mode:

```bash
cd voiceflow-ui
pnpm install
pnpm tauri dev
```

Run tests:

```bash
uv run pytest tests/ -v
```

The sidecar expects an X11 session and these system tools:

```bash
sudo apt install xclip xdotool libportaudio2
```

## Configuration

The sidecar reads the path in `VOICEFLOW_CONFIG`, or `config.yaml` beside the
sidecar when run directly. Configuration controls audio capture, the ASR
endpoint/model, and terminal-window detection.

The current ASR client uses the OpenAI-compatible endpoint
`/v1/audio/transcriptions`. The configured model name must match the model
served by vLLM.

## Packaging

Build the Tauri binary and Debian package with `scripts/build-deb.sh`. The
Python extensions bundled in the package must be built with Python 3.12,
because the installed runtime uses system Python 3.12.

The package installs:

- `/usr/bin/voiceflow`
- `/opt/voiceflow/voiceflow_py/daemon.py`
- `/opt/voiceflow/{audio_recorder,asr_client,window_detector,utils}.py`
- `/opt/voiceflow/{text_cleanup,minimax_cleanup}.py`
- `/usr/lib/systemd/user/voiceflow.service`

The package template is the only VoiceFlow systemd unit maintained in this
repository. The user-level drop-in under `~/.config/systemd/user/` is
machine-specific and must not be committed.

## Safety

- Keep ASR bound to loopback unless remote access is explicitly required.
- Do not add credentials or machine-local secrets to the repository.
- Do not run destructive system commands during development.
- Do not start or stop system services as part of tests.

## Change Guidelines

- Keep the Rust/Python boundary explicit and preserve the JSON Lines protocol.
- Put desktop integration and paste behavior in Rust.
- Put recording and ASR protocol behavior in the sidecar.
- Add or update tests for sidecar behavior before changing its protocol.
- Update the package script whenever sidecar files change.
