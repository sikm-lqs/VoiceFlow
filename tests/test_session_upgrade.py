import json
import os
import io
import time
from types import SimpleNamespace

import pytest
import voiceflow_session as session

from runtime_status import RuntimeStatus
from voiceflow_session import wait_for_idle


def test_status_contains_no_transcript_and_pending_blocks_new_recordings(tmp_path):
    status = RuntimeStatus(tmp_path)
    status.update("recording")
    data = json.loads((tmp_path / "status.json").read_text())
    assert data["state"] == "recording" and data["pid"] == os.getpid()
    assert "text" not in data
    assert not status.upgrade_pending()
    (tmp_path / "upgrade-pending").write_text(json.dumps({"pid": os.getpid(), "created_at": time.time()}))
    assert status.upgrade_pending()


def test_stale_pending_marker_does_not_permanently_block_input(tmp_path):
    status = RuntimeStatus(tmp_path)
    (tmp_path / "upgrade-pending").write_text(json.dumps({"pid": os.getpid(), "created_at": 0}))
    assert not status.upgrade_pending()
    (tmp_path / "upgrade-pending").write_text("incomplete")
    assert not status.upgrade_pending()


def test_busy_state_waits_until_idle_and_allows_paste_settle(tmp_path):
    status = RuntimeStatus(tmp_path)
    status.update("transcribing")
    times = []
    def sleep(seconds):
        times.append(seconds)
        status.update("idle")
    assert wait_for_idle(tmp_path, sleep=sleep, timeout=10) == "idle"
    assert times


def test_legacy_without_status_is_reported_explicitly(tmp_path):
    assert wait_for_idle(tmp_path, sleep=lambda t: None) == "legacy_unknown"


def test_continuous_busy_times_out_without_forced_restart(tmp_path):
    RuntimeStatus(tmp_path).update("recording")
    assert wait_for_idle(tmp_path, sleep=lambda t: None, timeout=0) == "busy"


def test_paste_ack_and_errors_do_not_mark_new_recording_idle(monkeypatch, tmp_path):
    from voiceflow_py import daemon
    status = RuntimeStatus(tmp_path)
    monkeypatch.setattr(daemon, "runtime_status", status)
    monkeypatch.setattr(daemon.sys, "stdout", io.StringIO())
    daemon.send({"type": "result", "text": "private transcript"})
    assert status.state == "pasting"
    assert "private transcript" not in (tmp_path / "status.json").read_text()
    daemon._paste_complete()
    assert status.state == "idle"
    status.update("recording")
    daemon._paste_complete()
    daemon.send({"type": "error", "message": "upgrade pending"})
    assert status.state == "recording"


@pytest.mark.parametrize("enabled,fresh,configured_uid", [
    ("disabled", False, None), ("masked", False, None),
    ("disabled", True, os.getuid()),
])
def test_login_does_not_reenable_disabled_service_on_upgrade(
        monkeypatch, tmp_path, enabled, fresh, configured_uid):
    from pathlib import Path
    calls = []
    def ctl(*args):
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout=enabled if args[0] == "is-enabled" else "")
    original = Path.read_text
    def read(path, *args, **kwargs):
        if str(path) == "/var/lib/voiceflow/session-policy.json":
            return json.dumps({"fresh_install": fresh, "version": "1.0.0",
                               "configured_uid": configured_uid})
        return original(path, *args, **kwargs)
    monkeypatch.setattr(session, "ctl", ctl)
    monkeypatch.setattr(Path, "read_text", read)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    assert session.bootstrap() == 0
    assert not any(c[0] in ("enable", "start", "restart") for c in calls)


@pytest.mark.parametrize("state, expected", [("idle", 0), ("legacy_unknown", 0), ("busy", 1)])
def test_cooperative_restart_has_gate_and_never_forces_busy_session(monkeypatch, tmp_path, state, expected):
    calls = []
    def ctl(*args):
        assert RuntimeStatus(tmp_path).upgrade_pending()
        calls.append(args)
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(session, "runtime_directory", lambda: tmp_path)
    monkeypatch.setattr(session, "wait_for_idle", lambda path: state)
    monkeypatch.setattr(session, "ctl", ctl)
    assert session.upgrade() == expected
    assert bool(calls) is (expected == 0)
    assert not (tmp_path / "upgrade-pending").exists()


def test_root_desktop_launch_refused_before_any_service_calls(monkeypatch):
    monkeypatch.setattr(session.os, "geteuid", lambda: 0)
    monkeypatch.setattr(session, "ctl", lambda *args: pytest.fail("root must never call user manager"))
    assert session.main() == 1


@pytest.mark.parametrize("manual", [False, True])
def test_first_session_initializes_autostart_even_when_opened_from_menu(monkeypatch, tmp_path, manual):
    from pathlib import Path
    calls = []
    state = {"enabled": "disabled"}
    def ctl(*args):
        calls.append(args)
        if args[0] == "enable":
            state["enabled"] = "enabled"
        return SimpleNamespace(returncode=0, stdout=state["enabled"] if args[0] == "is-enabled" else "inactive")
    original = Path.read_text
    def read(path, *args, **kwargs):
        if str(path) == "/var/lib/voiceflow/session-policy.json":
            return '{"fresh_install":true,"configured_uid":null,"version":"1.0.0"}'
        return original(path, *args, **kwargs)
    monkeypatch.setattr(session, "ctl", ctl)
    monkeypatch.setattr(Path, "read_text", read)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    assert session.bootstrap(manual=manual) == 0
    assert any(c[0] == "enable" for c in calls)
