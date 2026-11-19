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
from utils import load_runtime_config
from text_cleanup import TextCleaner
from runtime_status import RuntimeStatus
from history import HistoryRecorder

logger = logging.getLogger("voiceflow_daemon")

_send_lock = threading.Lock()
cleaner = TextCleaner()
history = HistoryRecorder()
runtime_status = None


def send(msg: dict) -> None:
    if runtime_status is not None:
        if msg.get("type") == "state":
            runtime_status.update(msg["state"])
        elif msg.get("type") == "result":
            runtime_status.update("pasting")
        elif msg.get("type") == "ready":
            runtime_status.update("idle")
        elif msg.get("type") == "error" and runtime_status.state not in ("recording", "pasting"):
            runtime_status.update("idle")
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


def _paste_complete():
    if runtime_status is not None and runtime_status.state == "pasting":
        runtime_status.update("idle")


def _discard(wav_path: str) -> None:
    try:
        os.unlink(wav_path)
    except OSError:
        pass


def _transcribe_and_report(wav_path: str, mode: str) -> None:
    try:
        result = asr.transcribe(wav_path)
    except (ASRTimeoutError, ASRUnavailableError) as e:
        history.record(text_raw="", text_cleaned="", wav_path=wav_path,
                       error=f"asr_failed:{type(e).__name__}")
        _discard(wav_path)
        send({"type": "error", "message": f"ASR failed: {type(e).__name__}"})
        return
    except ASREmptyError:
        history.record(text_raw="", text_cleaned="", wav_path=wav_path,
                       error="empty_speech")
        _discard(wav_path)
        send({"type": "error", "message": "empty speech"})
        return

    shortcut = None
    if mode == "terminal":
        shortcut = "ctrl+shift+v"
    elif detector:
        shortcut = detector.detect_shortcut()

    cleaned = cleaner.clean(result.text)
    history.record(
        text_raw=result.text,
        text_cleaned=cleaned.text,
        wav_path=wav_path,
        backend=result.backend,
        latency_ms=result.duration_ms,
        elapsed_ms=result.elapsed_ms,
        usage=result.usage,
        request_id=result.request_id,
        cleanup_mode=cleaned.mode,
        cleanup_ms=cleaned.elapsed_ms,
    )
    _discard(wav_path)
    send({
        "type": "result",
        "text": cleaned.text,
        "shortcut": shortcut,
        "backend": result.backend,
        "cleanup": {
            "mode": cleaned.mode,
            "elapsed_ms": cleaned.elapsed_ms,
            "fallback_reason": cleaned.fallback_reason,
        },
    })


def main() -> None:
    global asr, detector, cleaner, history, runtime_status

    app_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    defaults = os.path.join(app_root, "defaults.yaml")
    if not os.path.exists(defaults):
        defaults = os.path.join(app_root, "config.yaml")
    cfg = load_runtime_config(defaults, legacy_path=os.path.join(app_root, "config.yaml"))
    runtime_status = RuntimeStatus()
    cleanup_cfg = dict(cfg.get("cleanup", {}))
    override = os.environ.get("VOICEFLOW_CLEANUP_MODE")
    if override is not None:
        if override not in ("off", "rules", "minimax"):
            raise ValueError("VOICEFLOW_CLEANUP_MODE must be off, rules or minimax")
        cleanup_cfg.update(mode=override, enabled=override != "off")
    cleaner = TextCleaner(cleanup_cfg)

    hist_cfg = cfg.get("history", {}) or {}
    history = HistoryRecorder(
        enabled=hist_cfg.get("enabled", False),
        max_entries=hist_cfg.get("max_entries", 10),
        keep_audio=hist_cfg.get("keep_audio", True),
    )

    recorder = AudioRecorder(
        sample_rate=cfg["audio"]["sample_rate"],
        channels=cfg["audio"]["channels"],
        max_seconds=cfg["audio"].get("max_record_seconds", 60),
        pre_roll_ms=cfg["audio"].get("pre_roll_ms", 200),
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
    local_client = ASRClient(
        endpoint=cfg["asr"]["endpoint"],
        timeout=cfg["asr"].get("timeout_seconds", 20),
        language=cfg["asr"].get("language", "zh"),
        task=cfg["asr"].get("task", "transcribe"),
        prompt=cfg["asr"].get("prompt", ""),
        model=cfg["asr"].get("model", ""),
        max_retries=0 if fallback_client else 1,
    )

    minimax_cfg = ((cfg["asr"].get("backends") or {}).get("minimax") or {})
    minimax_client = None
    if minimax_cfg.get("enabled"):
        mm_env = minimax_cfg.get("api_key_env", "MINIMAX_ASR_KEY")
        mm_key = os.environ.get(mm_env, "") or minimax_cfg.get("api_key", "")
        if mm_key:
            from minimax_asr import MiniMaxASRClient
            minimax_client = MiniMaxASRClient(
                endpoint=minimax_cfg.get(
                    "endpoint", "https://api.minimaxi.com/v1/speech_to_text"),
                model=minimax_cfg.get("model", "asr-1.0"),
                api_key=mm_key,
                timeout=minimax_cfg.get("timeout_seconds", 20),
            )
        else:
            logger.warning("minimax backend enabled but %s not set; skipping", mm_env)

    # asr.prefer decides the backend chain:
    #   local   -> [local, dashscope]
    #   cloud   -> [dashscope, local]
    #   minimax -> [minimax, dashscope]   (skips minimax when unconfigured)
    prefer = cfg["asr"].get("prefer", "local")
    if prefer == "minimax" and minimax_client is not None:
        chain = [minimax_client]
        if fallback_client is not None:
            chain.append(fallback_client)
        asr = ASRRouter(chain)
    elif prefer == "cloud" and fallback_client is not None:
        asr = ASRRouter([fallback_client, local_client])
    else:
        asr = ASRRouter(local_client, fallback_client)

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
            if runtime_status.upgrade_pending():
                send({"type": "error", "message": "VoiceFlow upgrade pending; try again shortly"})
                continue
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
                # Benign race from rapid hotkey taps: the previous stop already
                # handled this session. Ignore instead of surfacing an error.
                logger.warning("stop_and_transcribe ignored: not recording")
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

        elif cmd_type == "paste_complete":
            _paste_complete()

        elif cmd_type == "ping":
            send({"type": "pong"})

        else:
            send({"type": "error", "message": f"unknown command: {cmd_type}"})

    cleaner.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
    main()
