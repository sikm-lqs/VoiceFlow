"""Private, text-free state for cooperative upgrades. No recording history."""
import json
import logging
import os
from pathlib import Path
import tempfile
import time

VERSION = "1.0.0"
logger = logging.getLogger("voiceflow.status")


def runtime_directory():
    base = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"))
    if not base.is_dir() or base.stat().st_uid != os.getuid():
        return None
    return base / "voiceflow"


class RuntimeStatus:
    def __init__(self, directory=None):
        self.directory = Path(directory) if directory is not None else runtime_directory()
        self.state = "idle"

    def upgrade_pending(self):
        if self.directory is None:
            return False
        try:
            data = json.loads((self.directory / "upgrade-pending").read_text())
            pid = data["pid"]
            if not isinstance(pid, int) or pid <= 0 or not 0 <= time.time() - data["created_at"] < 300:
                return False
            os.kill(pid, 0)
            return True
        except (OSError, ValueError, TypeError, KeyError):
            return False

    def update(self, state):
        self.state = state
        if self.directory is None:
            return
        name = None
        try:
            self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            fd, name = tempfile.mkstemp(prefix=".status-", dir=self.directory)
            with os.fdopen(fd, "w") as f:
                json.dump({"version": VERSION, "state": state, "pid": os.getpid(),
                           "updated_at": time.time()}, f)
            os.replace(name, self.directory / "status.json")
        except OSError:
            logger.warning("Could not update private runtime state")
        finally:
            if name and os.path.exists(name):
                os.unlink(name)
