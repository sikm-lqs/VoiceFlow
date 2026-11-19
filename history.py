"""Opt-in local transcription history for debugging.

Stores the most recent transcriptions (raw and cleaned text, audio copy,
latency, usage, cleanup info) under XDG_STATE_HOME/voiceflow/history as a
rolling JSONL window. Disabled by default; the directory and files are
created with 0700/0600 permissions and never leave the machine.

Privacy: this module intentionally persists transcript text and audio.
Keep it opt-in (history.enabled) and never log entry contents elsewhere.
"""
import itertools
import json
import logging
import os
import threading
import time
from pathlib import Path

logger = logging.getLogger("voice_input.history")


class HistoryRecorder:
    """Rolling window of the most recent transcriptions, persisted as JSONL."""

    def __init__(self, enabled=False, max_entries=10, keep_audio=True,
                 directory=None):
        self.enabled = bool(enabled)
        self.max_entries = max(1, int(max_entries))
        self.keep_audio = bool(keep_audio)
        if directory is None:
            base = Path(os.environ.get("XDG_STATE_HOME",
                                       Path.home() / ".local" / "state"))
            directory = base / "voiceflow" / "history"
        self.directory = Path(directory)
        self._index = self.directory / "history.jsonl"
        self._lock = threading.Lock()
        self._counter = itertools.count()
        if self.enabled:
            try:
                self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
                with self._lock:
                    self._trim()
            except OSError:
                logger.warning("history disabled: cannot create %s", self.directory)
                self.enabled = False

    def record(self, *, text_raw, text_cleaned, wav_path=None, backend="",
               latency_ms=None, elapsed_ms=None, usage=None, request_id="",
               cleanup_mode="", cleanup_ms=None, error=""):
        """Append one entry; copy the WAV before the caller discards it."""
        if not self.enabled:
            return
        entry = {
            "ts": round(time.time(), 3),
            "backend": backend,
            "latency_ms": round(latency_ms, 1) if latency_ms is not None else None,
            "elapsed_ms": round(elapsed_ms, 1) if elapsed_ms is not None else None,
            "usage": usage or {},
            "request_id": request_id,
            "cleanup_mode": cleanup_mode,
            "cleanup_ms": round(cleanup_ms, 1) if cleanup_ms is not None else None,
            "error": error,
            "text_raw": text_raw,
            "text_cleaned": text_cleaned,
            "audio_file": None,
        }
        if wav_path and self.keep_audio and not error:
            entry["audio_file"] = self._copy_audio(wav_path)
        line = json.dumps(entry, ensure_ascii=False)
        try:
            with self._lock:
                with open(self._index, "a", encoding="utf-8") as fh:
                    fh.write(line + "\n")
                os.chmod(self._index, 0o600)
                self._trim()
        except OSError:
            logger.warning("history append failed")
            return
        logger.info("[history] entry saved (window<=%d)", self.max_entries)

    def _copy_audio(self, wav_path):
        stamp = time.strftime("%Y%m%d-%H%M%S")
        target = self.directory / f"{stamp}-{next(self._counter):04d}.wav"
        try:
            target.write_bytes(Path(wav_path).read_bytes())
            os.chmod(target, 0o600)
            return target.name
        except OSError:
            logger.warning("history audio copy failed")
            return None

    def _trim(self):
        """Keep the last max_entries entries; drop orphaned audio files."""
        try:
            lines = self._index.read_text(encoding="utf-8").splitlines()
        except OSError:
            return
        keep = [ln for ln in lines[-self.max_entries:] if ln.strip()]
        keep_names = set()
        for line in keep:
            try:
                keep_names.add(json.loads(line).get("audio_file"))
            except ValueError:
                continue
        if len(keep) != len(lines):
            tmp = self._index.with_suffix(".tmp")
            tmp.write_text("".join(ln + "\n" for ln in keep), encoding="utf-8")
            os.replace(tmp, self._index)
        for path in self.directory.glob("*.wav"):
            if path.name not in keep_names:
                try:
                    path.unlink()
                except OSError:
                    pass

    def close(self):
        pass
