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


def test_asr_prefer_valid_values():
    cfg = _valid_config()
    cfg["asr"]["prefer"] = "cloud"
    path = _write_yaml(cfg)
    try:
        result = load_config(path)
        assert result["asr"]["prefer"] == "cloud"
    finally:
        os.unlink(path)


def test_asr_prefer_invalid_rejected():
    cfg = _valid_config()
    cfg["asr"]["prefer"] = "edge"
    path = _write_yaml(cfg)
    try:
        with pytest.raises(ConfigValidationError, match="prefer"):
            load_config(path)
    finally:
        os.unlink(path)


def test_history_section_validated():
    cfg = _valid_config()
    cfg["history"] = {"enabled": True, "max_entries": 10, "keep_audio": True}
    path = _write_yaml(cfg)
    try:
        load_config(path)
    finally:
        os.unlink(path)
    for bad in ({"enabled": "yes"}, {"max_entries": 0}, {"max_entries": "ten"},
                {"keep_audio": 1}, True, "nope"):
        cfg = _valid_config()
        cfg["history"] = bad
        path = _write_yaml(cfg)
        try:
            with pytest.raises(ConfigValidationError, match="history"):
                load_config(path)
        finally:
            os.unlink(path)


def test_asr_prefer_minimax_and_backends_validation():
    cfg = _valid_config()
    cfg["asr"]["prefer"] = "minimax"
    cfg["asr"]["backends"] = {"minimax": {
        "enabled": True,
        "endpoint": "https://api.minimaxi.com/v1/speech_to_text",
        "model": "asr-1.0", "api_key_env": "MINIMAX_ASR_KEY",
        "timeout_seconds": 20}}
    path = _write_yaml(cfg)
    try:
        load_config(path)
    finally:
        os.unlink(path)
    for bad in ("edge", "MINIMAX"):
        cfg = _valid_config()
        cfg["asr"]["prefer"] = bad
        path = _write_yaml(cfg)
        try:
            with pytest.raises(ConfigValidationError, match="prefer"):
                load_config(path)
        finally:
            os.unlink(path)
    for bad in ({"enabled": "on"}, {"timeout_seconds": 0},
                {"timeout_seconds": "20"}, {"endpoint": 5}):
        cfg = _valid_config()
        cfg["asr"]["backends"] = {"minimax": bad}
        path = _write_yaml(cfg)
        try:
            with pytest.raises(ConfigValidationError, match="minimax"):
                load_config(path)
        finally:
            os.unlink(path)
