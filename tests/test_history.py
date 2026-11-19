import json
import os
import stat
from pathlib import Path

import pytest

from history import HistoryRecorder


def _write_wav(path, size=1024):
    Path(path).write_bytes(b"RIFF" + b"\x00" * size)
    return path


def _entries(directory):
    index = Path(directory) / "history.jsonl"
    if not index.exists():
        return []
    return [json.loads(ln) for ln in
            index.read_text(encoding="utf-8").splitlines() if ln.strip()]


def test_disabled_records_nothing(tmp_path):
    recorder = HistoryRecorder(enabled=False, directory=tmp_path / "h")
    recorder.record(text_raw="你好", text_cleaned="你好",
                    wav_path=_write_wav(tmp_path / "a.wav"))
    assert not (tmp_path / "h").exists() or not any(
        (tmp_path / "h").iterdir())


def test_record_saves_text_metrics_and_audio(tmp_path):
    d = tmp_path / "h"
    recorder = HistoryRecorder(enabled=True, directory=d)
    recorder.record(
        text_raw="嗯，你好", text_cleaned="你好",
        wav_path=_write_wav(tmp_path / "a.wav"),
        backend="cloud", latency_ms=540.0, elapsed_ms=540.0,
        usage={"seconds": 3.88}, request_id="req-1",
        cleanup_mode="rules", cleanup_ms=0.4,
    )
    entries = _entries(d)
    assert len(entries) == 1
    e = entries[0]
    assert e["text_raw"] == "嗯，你好"
    assert e["text_cleaned"] == "你好"
    assert e["backend"] == "cloud"
    assert e["usage"] == {"seconds": 3.88}
    assert e["request_id"] == "req-1"
    assert e["cleanup_mode"] == "rules"
    assert e["error"] == ""
    assert e["audio_file"]
    assert (d / e["audio_file"]).read_bytes().startswith(b"RIFF")
    # privacy: index and audio are user-readable only
    assert stat.S_IMODE((d / "history.jsonl").stat().st_mode) == 0o600
    assert stat.S_IMODE((d / e["audio_file"]).stat().st_mode) == 0o600
    assert stat.S_IMODE(d.stat().st_mode) == 0o700


def test_window_trims_entries_and_orphan_audio(tmp_path):
    d = tmp_path / "h"
    recorder = HistoryRecorder(enabled=True, max_entries=3, directory=d)
    for i in range(5):
        recorder.record(text_raw=f"t{i}", text_cleaned=f"t{i}",
                        wav_path=_write_wav(tmp_path / f"w{i}.wav", size=16),
                        backend="cloud", usage={"seconds": 1})
    entries = _entries(d)
    assert [e["text_cleaned"] for e in entries] == ["t2", "t3", "t4"]
    wavs = sorted(p.name for p in d.glob("*.wav"))
    assert len(wavs) == 3
    kept = {e["audio_file"] for e in entries}
    assert set(wavs) == kept


def test_error_entry_keeps_no_audio(tmp_path):
    d = tmp_path / "h"
    recorder = HistoryRecorder(enabled=True, directory=d)
    recorder.record(text_raw="", text_cleaned="",
                    wav_path=_write_wav(tmp_path / "a.wav"),
                    error="asr_failed:ASRUnavailableError")
    entries = _entries(d)
    assert len(entries) == 1
    assert entries[0]["error"] == "asr_failed:ASRUnavailableError"
    assert entries[0]["audio_file"] is None
    assert not list(d.glob("*.wav"))


def test_keep_audio_false_stores_text_only(tmp_path):
    d = tmp_path / "h"
    recorder = HistoryRecorder(enabled=True, keep_audio=False, directory=d)
    recorder.record(text_raw="hi", text_cleaned="hi",
                    wav_path=_write_wav(tmp_path / "a.wav"))
    assert _entries(d)[0]["audio_file"] is None
    assert not list(d.glob("*.wav"))


def test_missing_wav_tolerated(tmp_path):
    d = tmp_path / "h"
    recorder = HistoryRecorder(enabled=True, directory=d)
    recorder.record(text_raw="x", text_cleaned="x",
                    wav_path=tmp_path / "never-existed.wav")
    e = _entries(d)[0]
    assert e["audio_file"] is None


def test_max_entries_floor_is_one(tmp_path):
    recorder = HistoryRecorder(enabled=True, max_entries=0, directory=tmp_path / "h")
    assert recorder.max_entries == 1


def test_bad_directory_disables_recorder(tmp_path, monkeypatch):
    bad = tmp_path / "file-not-dir"
    bad.write_text("x")
    recorder = HistoryRecorder(enabled=True, directory=bad / "sub")
    # mkdir fails because parent is a file -> feature disables, no crash
    assert recorder.enabled is False
    recorder.record(text_raw="x", text_cleaned="x")  # no-op, no exception
