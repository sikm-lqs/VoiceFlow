import yaml
import logging
import os
import logging.handlers


class ConfigValidationError(Exception):
    pass


def load_config(path: str = "config.yaml") -> dict:
    with open(path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    validate_config(cfg)
    return cfg


def validate_config(cfg: dict) -> None:
    if not isinstance(cfg, dict):
        raise ConfigValidationError("Config must be a YAML mapping")

    required_sections = ["hotkey", "audio", "asr", "paste"]
    for section in required_sections:
        if section not in cfg:
            raise ConfigValidationError(f"Missing required section: {section}")

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
