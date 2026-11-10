import yaml
import logging
import os
import logging.handlers
from copy import deepcopy
from pathlib import Path


class ConfigValidationError(Exception):
    pass


def load_config(path: str = "config.yaml") -> dict:
    with open(path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    validate_config(cfg)
    return cfg


def load_runtime_config(defaults_path, *, system_path="/etc/voiceflow/config.yaml",
                        legacy_path="/opt/voiceflow/config.yaml", user_path=None,
                        explicit_path=None):
    """Defaults < site (or legacy) < user < explicit. Never rewrite any layer."""
    if user_path is None:
        user_path = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "voiceflow/config.yaml"
    if explicit_path is None:
        explicit_path = os.environ.get("VOICEFLOW_CONFIG")

    def read(path, required=False):
        path = Path(path)
        if not path.exists():
            if required:
                raise ConfigValidationError(f"Missing configuration: {path}")
            return {}
        try:
            if path.stat().st_size > 1_048_576:
                raise ConfigValidationError(f"Configuration too large: {path}")
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError, RecursionError):
            raise ConfigValidationError(f"Cannot read configuration: {path}") from None
        if not isinstance(data, dict):
            raise ConfigValidationError(f"Configuration must be a mapping: {path}")
        return data

    def merge(base, overlay, depth=0):
        if depth > 32:
            raise ConfigValidationError("Configuration nesting is too deep")
        result = deepcopy(base)
        for key, value in overlay.items():
            if isinstance(value, dict) and isinstance(result.get(key), dict):
                result[key] = merge(result[key], value, depth + 1)
            else:
                result[key] = deepcopy(value)
        return result

    cfg = read(defaults_path, required=True)
    site = Path(system_path)
    paths = [site if site.exists() else Path(legacy_path), Path(user_path)]
    if explicit_path:
        paths.append(Path(explicit_path))
    for path in paths:
        cfg = merge(cfg, read(path, required=bool(explicit_path) and path == Path(explicit_path)))
    try:
        validate_config(cfg)
    except (TypeError, AttributeError):
        raise ConfigValidationError("Invalid configuration section types") from None
    return cfg


def validate_config(cfg: dict) -> None:
    if not isinstance(cfg, dict):
        raise ConfigValidationError("Config must be a YAML mapping")

    required_sections = ["hotkey", "audio", "asr", "paste"]
    for section in required_sections:
        if section not in cfg:
            raise ConfigValidationError(f"Missing required section: {section}")
        if not isinstance(cfg[section], dict):
            raise ConfigValidationError(f"{section} must be a mapping")

    h = cfg["hotkey"]
    if "smart_combo" not in h and "terminal_combo" not in h:
        raise ConfigValidationError("Missing hotkey.smart_combo or hotkey.terminal_combo")

    a = cfg["audio"]
    for field in ["sample_rate", "channels"]:
        if field not in a:
            raise ConfigValidationError(f"Missing audio.{field}")
        if not isinstance(a[field], int):
            raise ConfigValidationError(f"audio.{field} must be an integer")

    asr = cfg["asr"]
    if "endpoint" not in asr:
        raise ConfigValidationError("Missing asr.endpoint")

    if "cleanup" in cfg:
        cleanup = cfg["cleanup"]
        if not isinstance(cleanup, dict):
            raise ConfigValidationError("cleanup must be a mapping")
        if not isinstance(cleanup.get("enabled", False), bool):
            raise ConfigValidationError("cleanup.enabled must be a boolean")
        if cleanup.get("mode", "rules") not in ("off", "rules", "minimax"):
            raise ConfigValidationError("cleanup.mode must be off, rules or minimax")
        fillers = cleanup.get("fillers", ["呃", "嗯"])
        if not isinstance(fillers, list) or any(
            not isinstance(word, str) or not word or len(word) > 16 for word in fillers
        ):
            raise ConfigValidationError("cleanup.fillers must contain nonempty short strings")
        if "minimax" in cleanup and not isinstance(cleanup["minimax"], dict):
            raise ConfigValidationError("cleanup.minimax must be a mapping")


def setup_logging(level: str = "INFO", log_file: str = "") -> None:
    root_logger = logging.getLogger("voice_input")
    root_logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-5s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    console = logging.StreamHandler()
    console.setFormatter(fmt)
    root_logger.addHandler(console)

    if log_file:
        log_dir = os.path.dirname(log_file)
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(
            log_file, maxBytes=5 * 1024 * 1024, backupCount=3,
        )
        fh.setFormatter(fmt)
        root_logger.addHandler(fh)
