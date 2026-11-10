"""Run ONLY as the desktop user: session bootstrap and cooperative restart."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import subprocess
import time

from runtime_status import runtime_directory


def read_status(directory):
    try:
        path = Path(directory) / "status.json"
        if path.stat().st_size > 4096:
            return None
        data = json.loads(path.read_text())
        pid = data["pid"]
        if not isinstance(pid, int) or pid <= 0:
            return None
        os.kill(pid, 0)
        if data.get("state") not in ("idle", "recording", "transcribing", "recording_timeout", "pasting"):
            return None
        return data
    except (OSError, ValueError, KeyError, TypeError):
        return None


def wait_for_idle(directory, *, sleep=time.sleep, timeout=180):
    started = time.monotonic()
    path = Path(directory) / "status.json"
    if not path.exists():
        return "legacy_unknown"
    while time.monotonic() - started < timeout:
        data = read_status(directory)
        if data is None:
            return "unavailable"
        if data["state"] == "idle":
            # Python emits result before Rust has completed clipboard paste.
            sleep(1.0)
            again = read_status(directory)
            if again and again["state"] == "idle" and again["pid"] == data["pid"]:
                return "idle"
        sleep(0.25)
    return "busy"


def ctl(*args):
    return subprocess.run(["/usr/bin/systemctl", "--user", *args],
                          capture_output=True, text=True, timeout=15, check=False)


def upgrade():
    directory = runtime_directory()
    if directory is None:
        print("[VoiceFlow] No user runtime directory; restart deferred.")
        return 1
    directory.mkdir(mode=0o700, exist_ok=True)
    fd = os.open(directory / "upgrade.lock", os.O_WRONLY | os.O_CREAT, 0o600)
    lock = os.fdopen(fd, "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close()
        print("[VoiceFlow] Another cooperative restart is already queued.")
        return 0
    marker = directory / "upgrade-pending"
    # This is unprivileged: the user only ever controls their own service.
    try:
        fd = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump({"pid": os.getpid(), "created_at": time.time()}, f)
        state = wait_for_idle(directory)
        if state not in ("idle", "legacy_unknown"):
            print("[VoiceFlow] Busy/unavailable; no forced restart. Retry at next login.")
            return 1
        if state == "legacy_unknown":
            print("[VoiceFlow] Legacy app has no recording-state protocol; restarting once.")
        result = ctl("restart", "voiceflow.service")
        print("[VoiceFlow] Restart requested." if result.returncode == 0
              else "[VoiceFlow] Restart failed; inspect the user service journal.")
        return result.returncode
    finally:
        marker.unlink(missing_ok=True)
        lock.close()


def bootstrap(manual=False):
    names = [name for name in ("DISPLAY", "XAUTHORITY", "XDG_SESSION_TYPE")
             if os.environ.get(name)]
    if names:
        ctl("import-environment", *names)
    ctl("daemon-reload")
    enabled = ctl("is-enabled", "voiceflow.service").stdout.strip()
    if enabled.startswith("masked"):
        print("[VoiceFlow] Service is masked; leaving it unchanged.")
        return 0
    state_root = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "voiceflow"
    marker = state_root / "session-initialized"
    try:
        policy = json.loads(Path("/var/lib/voiceflow/session-policy.json").read_text())
    except (OSError, ValueError):
        policy = {}
    if (not manual and policy.get("fresh_install") and not marker.exists() and enabled == "disabled"
            and policy.get("configured_uid") != os.getuid()):
        # Respect an explicit per-user mask even on first login.
        ctl("enable", "voiceflow.service")
        enabled = ctl("is-enabled", "voiceflow.service").stdout.strip()
    state_root.mkdir(parents=True, mode=0o700, exist_ok=True)
    marker.touch(mode=0o600)
    if manual or enabled in ("enabled", "enabled-runtime", "linked", "linked-runtime"):
        active = ctl("is-active", "voiceflow.service").stdout.strip() == "active"
        directory = runtime_directory()
        status = read_status(directory) if directory is not None else None
        if active and (status is None or status.get("version") != policy.get("version", "1.0.0")):
            return upgrade()
        return ctl("start", "voiceflow.service").returncode
    return 0


def run_app():
    if os.environ.get("XDG_SESSION_TYPE") == "wayland":
        print("[VoiceFlow] v1.0 requires an X11 desktop session.")
        return 78
    os.environ.setdefault("DISPLAY", ":0")
    authority = Path(f"/run/user/{os.getuid()}/gdm/Xauthority")
    if authority.exists():
        os.environ.setdefault("XAUTHORITY", str(authority))
    for _ in range(30):
        try:
            ready = subprocess.run(["/usr/bin/xdotool", "getdisplaygeometry"],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2)
            if ready.returncode == 0:
                os.execv("/usr/bin/voiceflow", ["/usr/bin/voiceflow"])
        except subprocess.TimeoutExpired:
            pass
        time.sleep(1)
    print("[VoiceFlow] X11 unavailable; waiting for a desktop session.")
    return 78


def main():
    if os.geteuid() == 0:
        print("[VoiceFlow] Refusing to launch desktop UI as root.")
        return 1
    parser = argparse.ArgumentParser()
    parser.add_argument("--upgrade", action="store_true")
    parser.add_argument("--launch", action="store_true")
    parser.add_argument("--login", action="store_true")
    parser.add_argument("--run", action="store_true")
    args = parser.parse_args()
    try:
        if args.run:
            return run_app()
        return upgrade() if args.upgrade else bootstrap(manual=args.launch)
    except (OSError, subprocess.SubprocessError):
        print("[VoiceFlow] User service unavailable; inspect systemctl --user status voiceflow.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
