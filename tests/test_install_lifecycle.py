from pathlib import Path
from types import SimpleNamespace
import json
import os

import pytest
import yaml

from install_lifecycle import Installer, InstallError


class FakeSystem:
    def __init__(self, enabled="enabled", active="active", desktop=True):
        self.enabled, self.active, self.desktop = enabled, active, desktop
        self.calls = []

    def __call__(self, command, **kwargs):
        self.calls.append(command)
        args = command[command.index("--user") + 1:] if "--user" in command else command
        text, code = "", 0
        if args[0] == "is-enabled":
            text, code = self.enabled, 0 if self.enabled == "enabled" else 1
        elif args[0] == "is-active":
            text, code = self.active, 0 if self.active == "active" else 3
        elif args[0] == "show-environment":
            text = "DISPLAY=:0\nPRIVATE_KEY=do-not-log" if self.desktop else ""
        return SimpleNamespace(returncode=code, stdout=text, stderr="")


def setup(tmp_path, enabled="enabled", active="active", desktop=True, bus=True):
    fake = FakeSystem(enabled, active, desktop)
    if bus:
        p = tmp_path / "run/user/1000"
        p.mkdir(parents=True)
        (p / "bus").touch()
    account = SimpleNamespace(pw_name="alice", pw_uid=1000, pw_gid=1000, pw_dir="/home/alice")
    installer = Installer(root=tmp_path, env={"SUDO_USER": "alice"}, runner=fake,
                          account_lookup=lambda name: account)
    return installer, fake


def legacy_config(tmp_path):
    p = tmp_path / "opt/voiceflow/config.yaml"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("asr:\n  endpoint: http://old:8000\ncleanup:\n  enabled: false\n")
    return p


def test_upgrade_backs_up_before_unpack_then_migrates_and_schedules_restart(tmp_path, capsys):
    installer, fake = setup(tmp_path)
    old = legacy_config(tmp_path)
    original = old.read_bytes()
    binary = tmp_path / "usr/bin/voiceflow"
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"old-binary-fixture-not-executable")
    installer.preinst("upgrade", "0.1.0")
    old.unlink()  # simulate dpkg removing its old payload
    installer.postinst("configure")
    assert (tmp_path / "etc/voiceflow/config.yaml").read_bytes() == original
    backups = list((tmp_path / "var/lib/voiceflow/backups").glob("*/legacy-config.yaml"))
    assert backups[0].read_bytes() == original
    assert backups[0].stat().st_mode & 0o777 == 0o600
    assert (backups[0].parent / "runtime/usr/bin/voiceflow").read_bytes() == binary.read_bytes()
    assert any("systemd-run" in str(c) for c in fake.calls)
    assert not any("enable" in c for c in fake.calls)
    assert "do-not-log" not in capsys.readouterr().out


def test_existing_site_config_is_never_overwritten(tmp_path):
    installer, _ = setup(tmp_path)
    legacy_config(tmp_path)
    site = tmp_path / "etc/voiceflow/config.yaml"
    site.parent.mkdir(parents=True)
    site.write_text("# keep my comments\ncleanup:\n  mode: off\n")
    original = site.read_bytes()
    installer.preinst("upgrade", "0.1.1")
    installer.postinst("configure")
    assert site.read_bytes() == original


@pytest.mark.parametrize("enabled", ["disabled", "masked"])
def test_upgrade_preserves_disabled_or_masked_autostart(tmp_path, enabled):
    installer, fake = setup(tmp_path, enabled=enabled, active="inactive")
    installer.preinst("upgrade", "0.1.1")
    installer.postinst("configure")
    assert not any("enable" in c or "start" in c or "restart" in c for c in fake.calls)


def test_fresh_install_enables_and_starts_desktop_user(tmp_path):
    installer, fake = setup(tmp_path, enabled="disabled", active="inactive")
    installer.preinst("install")
    installer.postinst("configure")
    assert any("enable" in c for c in fake.calls)
    assert any("start" in c for c in fake.calls)


def test_no_bus_does_not_fail_or_try_to_start_ui(tmp_path):
    installer, fake = setup(tmp_path, bus=False)
    installer.preinst("install")
    installer.postinst("configure")
    assert fake.calls == []
    policy = json.loads((tmp_path / "var/lib/voiceflow/session-policy.json").read_text())
    assert policy["fresh_install"] is True


def test_restrictive_installer_umask_still_allows_user_to_read_new_config(tmp_path):
    installer, _ = setup(tmp_path, bus=False)
    previous = os.umask(0o077)
    try:
        installer.preinst("install")
        installer.postinst("configure")
    finally:
        os.umask(previous)
    assert (tmp_path / "etc/voiceflow").stat().st_mode & 0o777 == 0o755
    assert (tmp_path / "etc/voiceflow/config.yaml").stat().st_mode & 0o777 == 0o644


def test_unknown_install_user_does_not_guess_an_account(tmp_path):
    fake = FakeSystem()
    installer = Installer(root=tmp_path, env={}, runner=fake)
    installer.preinst("install")
    installer.postinst("configure")
    assert not fake.calls


def test_repeated_configure_does_not_restart_twice(tmp_path):
    installer, fake = setup(tmp_path)
    installer.preinst("upgrade", "0.1.1")
    installer.postinst("configure")
    fake.calls.clear()
    installer.postinst("configure")
    assert not fake.calls


def test_prerm_upgrade_never_stops_running_recording(tmp_path):
    installer, fake = setup(tmp_path)
    installer.prerm("upgrade")
    assert not fake.calls


def test_migration_does_not_broaden_secret_config_permissions(tmp_path):
    installer, _ = setup(tmp_path)
    old = legacy_config(tmp_path)
    old.chmod(0o600)
    installer.preinst("upgrade", "0.1.0")
    installer.postinst("configure")
    assert (tmp_path / "etc/voiceflow/config.yaml").stat().st_mode & 0o777 == 0o600


def test_bad_config_fails_before_restarting_and_backup_is_kept(tmp_path):
    installer, fake = setup(tmp_path)
    legacy_config(tmp_path).write_text("cleanup: [bad]\n")
    defaults = tmp_path / "opt/voiceflow/defaults.yaml"
    defaults.write_text((Path(__file__).resolve().parents[1] / "defaults.yaml").read_text())
    installer.preinst("upgrade", "0.1.0")
    fake.calls.clear()
    with pytest.raises(InstallError):
        installer.postinst("configure")
    assert not fake.calls
    assert installer.state()["complete"] is False


def test_upgrade_repairs_state_if_service_changes_after_preinst(tmp_path):
    installer, fake = setup(tmp_path)
    installer.preinst("upgrade", "0.1.0")
    fake.enabled, fake.active = "disabled", "inactive"
    fake.calls.clear()
    installer.postinst("configure")
    assert any("enable" in c for c in fake.calls)
    assert any("start" in c for c in fake.calls)
