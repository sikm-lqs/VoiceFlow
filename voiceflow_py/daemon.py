"""VoiceFlow Python sidecar — communicates with Tauri via stdin/stdout JSON lines."""

import json
import sys
import os
import logging
import threading
import time

# Add parent directory to path so we can import existing modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
# Add bundled python-libs for system install
libs_dir = os.path.join(os.path.dirname(__file__), "..", "python-libs")
if os.path.isdir(libs_dir):
    sys.path.insert(0, libs_dir)

from audio_recorder import AudioRecorder, MicNotFoundError
from asr_client import (ASRClient, CloudASRClient, ASRRouter,
                        ASRTimeoutError, ASRUnavailableError, ASREmptyError)
from window_detector import PasteModeDetector
from utils import load_config

logger = logging.getLogger("voiceflow_daemon")

_send_lock = threading.Lock()


def send(msg: dict) -> None:
    line = json.dumps(msg, ensure_ascii=False) + "\n"
    with _send_lock:
        sys.stdout.write(line)
        sys.stdout.flush()


def _report_level(recorder: AudioRecorder) -> None:
    """Emit mic level ~10x/s while recording; handle 60s auto-stop."""
    while recorder.is_recording:
        send({"type": "level", "level": recorder.get_level()})
        time.sleep(0.1)

    if not recorder.auto_stopped:
        return

    # Recording hit the max-duration cap without a hotkey release:
    # tell Rust to clear its recording state, then transcribe anyway.
    send({"type": "state", "state": "recording_timeout"})
    wav_path = recorder.auto_wav_path
    recorder.auto_wav_path = ""
    if not wav_path:
        send({"type": "error", "message": "no audio captured"})
        return
    send({"type": "state", "state": "transcribing"})
    _transcribe_and_report(wav_path, "smart")


def _transcribe_and_report(wav_path: str, mode: str) -> None:
    try:
        result = asr.transcribe(wav_path)
    except (ASRTimeoutError, ASRUnavailableError) as e:
        send({"type": "error", "message": f"ASR failed: {type(e).__name__}"})
        return
    except ASREmptyError:
        send({"type": "error", "message": "empty speech"})
        return
    finally:
        try:
            os.unlink(wav_path)
        except OSError:
            pass

    shortcut = None
    if mode == "terminal":
        shortcut = "ctrl+shift+v"
    elif detector:
        shortcut = detector.detect_shortcut()

    send({
        "type": "result",
        "text": result.text,
        "shortcut": shortcut,
        "backend": result.backend,
    })


def main() -> None:
    global asr, detector

    config_path = os.environ.get("VOICEFLOW_CONFIG", os.path.join(os.path.dirname(__file__), "..", "config.yaml"))
    cfg = load_config(config_path)

    recorder = AudioRecorder(
        sample_rate=cfg["audio"]["sample_rate"],
        channels=cfg["audio"]["channels"],
        max_seconds=cfg["audio"].get("max_record_seconds", 60),
    )

    fb = cfg["asr"].get("fallback", {}) or {}
    fallback_client = None
    if fb.get("enabled"):
        api_key = (os.environ.get(fb.get("api_key_env", "DASHSCOPE_API_KEY"), "")
                   or fb.get("api_key", ""))
        fallback_client = CloudASRClient(
            endpoint=fb.get("endpoint",
                            "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"),
            model=fb.get("model", "qwen3-asr-flash"),
            api_key=api_key,
            timeout=fb.get("timeout_seconds", 20),
            language=cfg["asr"].get("language", ""),
            enable_itn=fb.get("enable_itn", True),
        )
    asr = ASRRouter(
        ASRClient(
            endpoint=cfg["asr"]["endpoint"],
            timeout=cfg["asr"].get("timeout_seconds", 20),
            language=cfg["asr"].get("language", "zh"),
            task=cfg["asr"].get("task", "transcribe"),
            prompt=cfg["asr"].get("prompt", ""),
            model=cfg["asr"].get("model", ""),
            max_retries=0 if fallback_client else 1,
        ),
        fallback_client,
    )

    paste_config = cfg.get("paste", {})
    detector = None
    if paste_config.get("smart_mode", True):
        detector = PasteModeDetector(
            default_shortcut=paste_config.get("default_shortcut", "ctrl+v"),
            terminal_shortcut=paste_config.get("terminal_shortcut", "ctrl+shift+v"),
            terminal_classes=paste_config.get("terminal_classes", []),
            terminal_title_keywords=paste_config.get("terminal_title_keywords", []),
        )

    send({"type": "ready"})

    level_thread: threading.Thread | None = None

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            cmd = json.loads(line)
        except json.JSONDecodeError:
            send({"type": "error", "message": "invalid json"})
            continue

        cmd_type = cmd.get("type", "")

        if cmd_type == "start_recording":
            try:
                recorder.start()
                if level_thread is None or not level_thread.is_alive():
                    level_thread = threading.Thread(
                        target=_report_level, args=(recorder,), daemon=True)
                    level_thread.start()
                send({"type": "state", "state": "recording"})
            except MicNotFoundError as e:
                send({"type": "error", "message": str(e)})

        elif cmd_type == "stop_and_transcribe":
            mode = cmd.get("mode", "smart")

            if not recorder.is_recording:
                send({"type": "error", "message": "not recording"})
                continue

            try:
                data, wav_path = recorder.stop()
            except RuntimeError as e:
                send({"type": "error", "message": str(e)})
                continue

            if not wav_path:
                send({"type": "error", "message": "no audio captured"})
                continue

            send({"type": "state", "state": "transcribing"})
            _transcribe_and_report(wav_path, mode)

        elif cmd_type == "ping":
            send({"type": "pong"})

        else:
            send({"type": "error", "message": f"unknown command: {cmd_type}"})


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
    main()
