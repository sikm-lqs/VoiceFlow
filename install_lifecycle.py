#!/usr/bin/python3 -I
"""Standalone maintainer script; preinst must use the standard library only.

Copied verbatim into DEBIAN/{preinst,postinst,prerm,postrm}. Never operate a
root GUI or guess another desktop account. Tests inject a root and runner.
"""
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime, timezone

VERSION = "1.0.0"


class InstallError(Exception):
    pass


class Installer:
    def __init__(self, root=Path("/"), env=None, runner=subprocess.run,
                 account_lookup=pwd.getpwnam):
        self.root = Path(root)
        self.env = dict(os.environ if env is None else env)
        self.runner, self.account_lookup = runner, account_lookup
        self.state_root = self.path("/var/lib/voiceflow")
        self.state_file = self.state_root / "install-state.json"

    def path(self, value):
        return self.root / str(value).lstrip("/")

    def atomic(self, path, data, mode=0o600):
        new_parent = not path.parent.exists()
        path.parent.mkdir(parents=True, mode=0o755, exist_ok=True)
        if new_parent:
            path.parent.chmod(0o755)
        if path.is_symlink():
            raise InstallError("Refusing a symlink at a managed installer path")
        fd, temporary = tempfile.mkstemp(prefix=".voiceflow-", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as f:
                os.fchmod(f.fileno(), mode)
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def save(self, data):
        self.atomic(self.state_file, json.dumps(data).encode())

    def state(self):
        try:
            state = json.loads(self.state_file.read_text())
            if not isinstance(state, dict) or "version" not in state:
                raise InstallError("Invalid installer state")
            return state
        except FileNotFoundError:
            return None

    def account(self):
        name = self.env.get("VOICEFLOW_INSTALL_USER") or self.env.get("SUDO_USER")
        if not name and self.env.get("PKEXEC_UID", "").isdigit():
            try:
                name = pwd.getpwuid(int(self.env["PKEXEC_UID"])).pw_name
            except KeyError:
                return None
        if not name or name == "root":
            return None
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]*\$?", name):
            raise InstallError("Invalid installation username")
        try:
            entry = self.account_lookup(name)
        except KeyError:
            raise InstallError("Installation user does not exist") from None
        if entry.pw_uid < 1000:
            raise InstallError("Refusing to configure a system account as a desktop user")
        return {"name": entry.pw_name, "uid": entry.pw_uid, "home": entry.pw_dir}

    def user_run(self, user, args):
        # A minimal environment avoids forwarding root variables or API keys.
        command = ["/usr/sbin/runuser", "-u", user["name"], "--", "/usr/bin/env", "-i",
                   f"HOME={user['home']}", f"USER={user['name']}", f"LOGNAME={user['name']}",
                   "PATH=/usr/bin:/bin", "LANG=C.UTF-8",
                   f"XDG_RUNTIME_DIR=/run/user/{user['uid']}",
                   f"DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/{user['uid']}/bus", *args]
        try:
            return self.runner(command, capture_output=True, text=True, timeout=20, check=False)
        except (OSError, subprocess.SubprocessError):
            return subprocess.CompletedProcess(command, 1, "", "")

    def ctl(self, user, *args):
        return self.user_run(user, ["/usr/bin/systemctl", "--user", *args])

    def has_bus(self, user):
        return self.path(f"/run/user/{user['uid']}/bus").exists()

    def preinst(self, action, old_version=""):
        if action not in ("install", "upgrade"):
            return
        existing = self.state()
        if existing and existing.get("complete") is False and existing.get("version") == VERSION:
            return  # interrupted unpack/configure: preserve the original snapshot
        user = self.account()
        generation = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
        self.state_root.mkdir(parents=True, mode=0o755, exist_ok=True)
        self.state_root.chmod(0o755)
        backup = self.state_root / "backups" / generation
        backup.mkdir(parents=True, mode=0o700)
        # The parent holds no secrets; backups and state remain root-private.
        (self.state_root / "backups").chmod(0o700)
        metadata = {}
        for source, name in [
            ("/opt/voiceflow/config.yaml", "legacy-config.yaml"),
            ("/etc/voiceflow/config.yaml", "site-config.yaml"),
            ("/usr/lib/systemd/user/voiceflow.service", "voiceflow.service"),
        ]:
            path = self.path(source)
            if path.is_file():
                self.atomic(backup / name, path.read_bytes())
                st = path.stat()
                metadata[name] = {"mode": st.st_mode & 0o777, "uid": st.st_uid, "gid": st.st_gid}
        for relative in [
            "usr/bin/voiceflow", "usr/bin/voiceflow-session",
            "opt/voiceflow/voiceflow_py/daemon.py",
            *("opt/voiceflow/" + name + ".py" for name in (
                "audio_recorder", "asr_client", "window_detector", "utils", "text_cleanup",
                "minimax_cleanup", "runtime_status", "voiceflow_session", "install_lifecycle")),
        ]:
            source = self.path(relative)
            if source.is_file():
                self.atomic(backup / "runtime" / relative, source.read_bytes(),
                            source.stat().st_mode & 0o777)
        if user:
            dropins = self.path(user["home"]) / ".config/systemd/user/voiceflow.service.d"
            if dropins.is_dir():
                shutil.copytree(dropins, backup / "user-dropins", symlinks=True)
        enabled, active = "unknown", "unknown"
        if user and self.has_bus(user):
            enabled = self.ctl(user, "is-enabled", "voiceflow.service").stdout.strip()
            active = self.ctl(user, "is-active", "voiceflow.service").stdout.strip()
        self.save({"version": VERSION, "generation": generation,
                   "backup": str(backup.relative_to(self.root)), "user": user,
                   "config_metadata": metadata,
                   "fresh_install": action == "install" and not old_version,
                   "enabled": enabled, "active": active, "complete": False})
        print("[VoiceFlow] Configuration backup created; existing service left running.")

    def postinst(self, action):
        if action != "configure":
            return
        state = self.state()
        if state is None:
            self.preinst("upgrade", "unknown")
            state = self.state()
        if state.get("complete"):
            return
        site = self.path("/etc/voiceflow/config.yaml")
        if not site.exists():
            backup = self.root / state["backup"]
            old = backup / "site-config.yaml"
            if not old.exists():
                old = backup / "legacy-config.yaml"
            # Runtime-owned config: never shipped as a dpkg payload/conffile.
            # Preserve original bytes/comments, never edit an existing site file.
            metadata = state.get("config_metadata", {}).get(old.name, {})
            self.atomic(site, old.read_bytes() if old.exists() else b"{}\n", metadata.get("mode", 0o644))
            if metadata and os.geteuid() == 0:
                os.chown(site, metadata["uid"], metadata["gid"])
        self.validate_configuration(site)
        policy = {"version": VERSION, "fresh_install": state["fresh_install"],
                  "configured_uid": None}
        self.atomic(self.state_root / "session-policy.json", json.dumps(policy).encode(), 0o644)
        user = state["user"]
        result = "next-login"
        if user and self.has_bus(user):
            self.ctl(user, "daemon-reload")
            masked = state["enabled"].startswith("masked")
            current_enabled = self.ctl(user, "is-enabled", "voiceflow.service").stdout.strip()
            if state["fresh_install"] and not masked:
                reply = self.ctl(user, "enable", "voiceflow.service")
                if reply.returncode == 0:
                    policy["configured_uid"] = user["uid"]
                    self.atomic(self.state_root / "session-policy.json", json.dumps(policy).encode(), 0o644)
            elif (state["enabled"] in ("enabled", "enabled-runtime")
                  and current_enabled != state["enabled"] and not current_enabled.startswith("masked")):
                # Restore the snapshot if unit-file state changed after preinst.
                options = ["enable"]
                if state["enabled"] == "enabled-runtime":
                    options.append("--runtime")
                self.ctl(user, *options, "voiceflow.service")
            env = self.ctl(user, "show-environment").stdout
            graphical = any(line.startswith("DISPLAY=") and line != "DISPLAY=" for line in env.splitlines())
            current_active = self.ctl(user, "is-active", "voiceflow.service").stdout.strip()
            if graphical and not masked and state["active"] == "active" and current_active != "active":
                # No live recording exists in a stopped service.
                reply = self.ctl(user, "--no-block", "start", "voiceflow.service")
                result = "previous-running-service-restored" if reply.returncode == 0 else "start-deferred"
            elif graphical and not masked and state["active"] == "active":
                reply = self.user_run(user, [
                    "/usr/bin/systemd-run", "--user", "--collect",
                    "--unit=voiceflow-upgrade-" + state["generation"],
                    "--property=Type=exec", "/usr/bin/python3",
                    "/opt/voiceflow/voiceflow_session.py", "--upgrade",
                ])
                result = "cooperative-restart-scheduled" if reply.returncode == 0 else "restart-deferred"
            elif graphical and not masked and state["fresh_install"]:
                reply = self.ctl(user, "--no-block", "start", "voiceflow.service")
                result = "start-requested" if reply.returncode == 0 else "start-deferred"
            elif not state["fresh_install"]:
                result = "previous-enabled-and-running-state-preserved"
        state.update(complete=True, service_result=result)
        self.save(state)
        print(f"[VoiceFlow] v{VERSION} configured; MiniMax remains opt-in.")
        print(f"[VoiceFlow] User service: {result}. Config: /etc/voiceflow/config.yaml")
        print("[VoiceFlow] Private backups: /var/lib/voiceflow/backups")

    def validate_configuration(self, site):
        app = self.path("/opt/voiceflow")
        defaults = app / "defaults.yaml"
        if not defaults.exists():
            # Unit tests may deliberately exercise pre-unpack state.
            if self.root == Path("/"):
                raise InstallError("Missing package defaults")
            return
        sys.path.insert(0, str(app))
        sys.path.insert(0, str(app / "python-libs"))
        from utils import load_runtime_config, ConfigValidationError
        from text_cleanup import TextCleaner
        try:
            cfg = load_runtime_config(defaults, system_path=site,
                                      legacy_path=self.path("/nonexistent-legacy"),
                                      user_path=self.path("/nonexistent-user"), explicit_path=False)
            TextCleaner(cfg["cleanup"]).close()
        except (ConfigValidationError, ValueError):
            raise InstallError("Invalid runtime configuration; backup preserved") from None

    def prerm(self, action):
        if action != "remove":
            return
        user = self.account()
        if user and self.has_bus(user):
            self.ctl(user, "disable", "--now", "voiceflow.service")

    def postrm(self, action):
        # Keep configuration/backups on remove and purge for explicit recovery.
        # Never walk arbitrary home directories or run a root desktop process.
        return


def main():
    root = Path(os.environ.get("DPKG_ROOT") or "/").resolve()
    # dpkg --root=... --force-not-root is supported for offline rootfs tests.
    # Never touch a real user session while operating on an alternate root.
    if os.geteuid() != 0 and (root == Path("/") or not root.is_dir()
                             or root.stat().st_uid != os.geteuid()):
        print("[VoiceFlow] Installer lifecycle requires root.", file=sys.stderr)
        return 1
    phase = Path(sys.argv[0]).name.rsplit(".", 1)[-1]
    args = sys.argv[1:]
    if phase not in ("preinst", "postinst", "prerm", "postrm"):
        if not args:
            return 1
        phase, args = args[0], args[1:]
    env = os.environ if root == Path("/") else {}
    try:
        installer = Installer(root=root, env=env)
        getattr(installer, phase)(*args[:2] if phase == "preinst" else args[:1])
    except (InstallError, OSError, ValueError, TypeError):
        print("[VoiceFlow] Configuration failed; backups preserved. No secret contents logged.",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
