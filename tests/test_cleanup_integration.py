from pathlib import Path
from types import SimpleNamespace

import pytest

from text_cleanup import TextCleaner
from utils import ConfigValidationError, validate_config
from voiceflow_py import daemon


def valid_config():
    return {
        "hotkey": {"smart_combo": "f10"},
        "audio": {"sample_rate": 16000, "channels": 1},
        "asr": {"endpoint": "http://127.0.0.1:8000"}, "paste": {},
    }


def test_sidecar_cleans_after_asr_and_keeps_paste_protocol(monkeypatch, tmp_path):
    wav = tmp_path / "audio.wav"
    wav.write_bytes(b"fake")
    messages = []
    monkeypatch.setattr(daemon, "asr", SimpleNamespace(
        transcribe=lambda path: SimpleNamespace(text="呃，测试 Qwen3-ASR。", backend="local")
    ), raising=False)
    monkeypatch.setattr(daemon, "cleaner", TextCleaner({"enabled": True}), raising=False)
    monkeypatch.setattr(daemon, "detector", None, raising=False)
    monkeypatch.setattr(daemon, "send", messages.append)
    daemon._transcribe_and_report(str(wav), "terminal")
    assert not wav.exists()
    assert messages[0]["type"] == "result"
    assert messages[0]["text"] == "测试 Qwen3-ASR。"
    assert messages[0]["shortcut"] == "ctrl+shift+v"
    assert messages[0]["backend"] == "local"
    assert messages[0]["cleanup"]["mode"] == "rules"
    assert "raw_text" not in messages[0]


def test_sidecar_missing_cleanup_config_keeps_exact_text(monkeypatch, tmp_path):
    wav = tmp_path / "audio.wav"
    wav.write_bytes(b"fake")
    messages = []
    monkeypatch.setattr(daemon, "asr", SimpleNamespace(
        transcribe=lambda path: SimpleNamespace(text="呃，测试。", backend="cloud")
    ), raising=False)
    monkeypatch.setattr(daemon, "cleaner", TextCleaner(), raising=False)
    monkeypatch.setattr(daemon, "detector", SimpleNamespace(detect_shortcut=lambda: "ctrl+v"), raising=False)
    monkeypatch.setattr(daemon, "send", messages.append)
    daemon._transcribe_and_report(str(wav), "smart")
    assert messages[0]["text"] == "呃，测试。"
    assert messages[0]["backend"] == "cloud"


@pytest.mark.parametrize("cleanup", [
    [], {"enabled": "yes"}, {"mode": "unknown"}, {"fillers": "嗯"},
    {"fillers": [""]}, {"fillers": [3]},
])
def test_invalid_cleanup_configuration_rejected(cleanup):
    cfg = valid_config()
    cfg["cleanup"] = cleanup
    with pytest.raises(ConfigValidationError, match="cleanup"):
        validate_config(cfg)


def test_missing_or_disabled_cleanup_config_is_compatible():
    validate_config(valid_config())
    cfg = valid_config()
    cfg["cleanup"] = {"enabled": False, "mode": "rules", "fillers": []}
    validate_config(cfg)


def test_new_python_modules_included_in_package_sources():
    root = Path(__file__).resolve().parents[1]
    pyproject = (root / "pyproject.toml").read_text()
    builder = (root / "scripts/build-deb.sh").read_text()
    for name in ("text_cleanup", "minimax_cleanup"):
        assert '"%s"' % name in pyproject
        assert '$PROJECT_ROOT/%s.py' % name in builder
