from pathlib import Path
import yaml
import pytest

from utils import load_runtime_config, ConfigValidationError

ROOT = Path(__file__).resolve().parents[1]


def write(path, data):
    path.write_text(yaml.safe_dump(data, allow_unicode=True))
    return path


def load(tmp_path, **kwargs):
    return load_runtime_config(
        ROOT / "config.yaml", system_path=tmp_path / "site.yaml",
        user_path=tmp_path / "user.yaml", legacy_path=tmp_path / "legacy.yaml",
        explicit_path=kwargs.get("explicit"),
    )


def test_old_config_gets_rules_default_without_rewriting_file(tmp_path):
    legacy = write(tmp_path / "legacy.yaml", {"asr": {"endpoint": "http://old:8000"}})
    before = legacy.read_bytes()
    cfg = load(tmp_path)
    assert cfg["asr"]["endpoint"] == "http://old:8000"
    assert cfg["cleanup"]["enabled"] is True
    assert cfg["cleanup"]["mode"] == "rules"
    assert legacy.read_bytes() == before


def test_layers_and_explicit_disabled_are_preserved(tmp_path):
    write(tmp_path / "legacy.yaml", {"asr": {"endpoint": "http://ignored"}})
    write(tmp_path / "site.yaml", {"asr": {"endpoint": "http://site"}, "cleanup": {"enabled": False}})
    write(tmp_path / "user.yaml", {"asr": {"language": "en"}})
    explicit = write(tmp_path / "explicit.yaml", {"asr": {"timeout_seconds": 9}})
    cfg = load(tmp_path, explicit=explicit)
    assert cfg["asr"]["endpoint"] == "http://site"
    assert cfg["asr"]["language"] == "en"
    assert cfg["asr"]["timeout_seconds"] == 9
    assert cfg["cleanup"]["enabled"] is False


def test_list_overrides_replace_not_append(tmp_path):
    write(tmp_path / "user.yaml", {"cleanup": {"fillers": []}})
    assert load(tmp_path)["cleanup"]["fillers"] == []


@pytest.mark.parametrize("data", [[], {"cleanup": None}, {"cleanup": {"enabled": "yes"}}])
def test_invalid_overrides_fail_with_generic_error(tmp_path, data):
    write(tmp_path / "user.yaml", data)
    with pytest.raises(ConfigValidationError):
        load(tmp_path)


def test_missing_explicit_config_is_not_silently_ignored(tmp_path):
    with pytest.raises(ConfigValidationError):
        load(tmp_path, explicit=tmp_path / "missing.yaml")
