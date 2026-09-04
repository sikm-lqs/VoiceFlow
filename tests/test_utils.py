import pytest
import tempfile
import os
import yaml

from utils import load_config, validate_config, ConfigValidationError


def _write_yaml(data: dict) -> str:
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
    yaml.dump(data, f)
    f.close()
    return f.name


def _valid_config() -> dict:
    return {
        "hotkey": {"smart_combo": "f10", "terminal_combo": "f11"},
        "audio": {"sample_rate": 16000, "channels": 1, "dtype": "int16", "max_record_seconds": 60},
        "asr": {"endpoint": "http://127.0.0.1:8000", "timeout_seconds": 20},
        "paste": {
            "enabled": True,
            "smart_mode": True,
            "default_shortcut": "ctrl+v",
            "terminal_shortcut": "ctrl+shift+v",
        },
    }


def test_load_valid_config():
    path = _write_yaml(_valid_config())
    try:
        cfg = load_config(path)
        assert cfg["hotkey"]["smart_combo"] == "f10"
        assert cfg["audio"]["sample_rate"] == 16000
    finally:
        os.unlink(path)


def test_missing_section():
    cfg = _valid_config()
    del cfg["hotkey"]
    path = _write_yaml(cfg)
    try:
        with pytest.raises(ConfigValidationError, match="hotkey"):
            load_config(path)
    finally:
        os.unlink(path)


def test_missing_audio_sample_rate():
    cfg = _valid_config()
    del cfg["audio"]["sample_rate"]
    path = _write_yaml(cfg)
    try:
        with pytest.raises(ConfigValidationError, match="sample_rate"):
            load_config(path)
    finally:
        os.unlink(path)


def test_invalid_sample_rate_type():
    cfg = _valid_config()
    cfg["audio"]["sample_rate"] = "abc"
    path = _write_yaml(cfg)
    try:
        with pytest.raises(ConfigValidationError, match="integer"):
            load_config(path)
    finally:
        os.unlink(path)


def test_missing_asr_endpoint():
    cfg = _valid_config()
    del cfg["asr"]["endpoint"]
    path = _write_yaml(cfg)
    try:
        with pytest.raises(ConfigValidationError, match="endpoint"):
            load_config(path)
    finally:
        os.unlink(path)


def test_missing_hotkey_combo():
    cfg = _valid_config()
    del cfg["hotkey"]["smart_combo"]
    del cfg["hotkey"]["terminal_combo"]
    path = _write_yaml(cfg)
    try:
        with pytest.raises(ConfigValidationError, match="smart_combo|terminal_combo"):
            load_config(path)
    finally:
        os.unlink(path)


def test_missing_optional_sections_ok():
    cfg = _valid_config()
    path = _write_yaml(cfg)
    try:
        result = load_config(path)
        assert "ux" not in result
        assert "log" not in result
    finally:
        os.unlink(path)
