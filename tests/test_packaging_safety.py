"""Release defaults and upgrade safety; no service or installation side effects."""
import json
from pathlib import Path
import re
import subprocess
import sys
import tomllib

from utils import load_config
from text_cleanup import TextCleaner

ROOT = Path(__file__).resolve().parents[1]


def test_shipped_config_never_enables_minimax_by_default():
    cfg = load_config(str(ROOT / "config.yaml"))
    cleaner = TextCleaner(cfg["cleanup"])
    assert cleaner.mode == "rules"
    assert cleaner.smart is None


def test_release_versions_match():
    version = "1.0.0"
    assert tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"] == version
    assert json.loads((ROOT / "voiceflow-ui/package.json").read_text())["version"] == version
    assert json.loads((ROOT / "voiceflow-ui/src-tauri/tauri.conf.json").read_text())["version"] == version
    assert tomllib.loads((ROOT / "voiceflow-ui/src-tauri/Cargo.toml").read_text())["package"]["version"] == version
    assert f'VERSION="{version}"' in (ROOT / "scripts/build-deb.sh").read_text()


def test_builder_separates_defaults_and_preserves_old_artifacts():
    builder = (ROOT / "scripts/build-deb.sh").read_text()
    assert '/defaults.yaml"' in builder
    assert '/opt/voiceflow/config.yaml"' not in builder
    assert 'Pre-Depends: python3 (>= 3.12), python3 (<< 3.13)' in builder
    assert 'rm -rf "$BUILD_DIR"' not in builder
    assert 'PYTHON_VERSION' in builder and '"3.12"' in builder
    assert '".venv/bin/pip"' not in builder
    assert 'dpkg-deb --root-owner-group --build' in builder


def test_maintainer_scripts_delegate_to_tested_lifecycle_module():
    builder = (ROOT / "scripts/build-deb.sh").read_text()
    assert 'for phase in preinst postinst prerm postrm' in builder
    assert '"$PROJECT_ROOT/install_lifecycle.py"' in builder
    assert (ROOT / "install_lifecycle.py").read_text().startswith("#!/usr/bin/python3 -I\n")


def test_documented_config_merge_preserves_asr_and_file_permissions(tmp_path):
    import yaml
    path = tmp_path / "config.yaml"
    cfg = load_config(str(ROOT / "config.yaml"))
    cfg.pop("cleanup")
    path.write_text(yaml.safe_dump(cfg, allow_unicode=True))
    path.chmod(0o640)
    docs = (ROOT / "docs/UPGRADE_0.1.1.md").read_text()
    snippet = re.search(r"sudo /usr/bin/python3 - <<'PY'\n(.*?)\nPY", docs, re.S).group(1)
    snippet = snippet.replace('Path("/opt/voiceflow/config.yaml")', f"Path({str(path)!r})")
    subprocess.run([sys.executable, "-B", "-c", snippet], cwd=ROOT, check=True,
                   capture_output=True, text=True)
    updated = yaml.safe_load(path.read_text())
    assert updated.pop("cleanup") == {"enabled": True, "mode": "rules", "fillers": ["呃", "嗯"]}
    assert updated == cfg
    assert path.stat().st_mode & 0o777 == 0o640
