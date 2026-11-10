#!/usr/bin/env python3
"""Exercise dpkg in temporary roots ONLY. No host package or service changes.

Requires fakeroot. The synthetic old packages have no maintainer scripts;
the real new package sees DPKG_ROOT and never contacts host user services.
"""
import argparse
import os
from pathlib import Path
import subprocess
import tempfile


def run(command, **kwargs):
    result = subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True,
                            text=True, timeout=120, **kwargs)
    if result.returncode:
        raise RuntimeError("Isolated command failed:\n" + result.stdout[-3000:] + result.stderr[-3000:])
    return result.stdout


def old_package(directory, version):
    stage = directory / "old"
    (stage / "DEBIAN").mkdir(parents=True)
    (stage / "DEBIAN/control").write_text(
        f"Package: voiceflow\nVersion: {version}\nArchitecture: amd64\n"
        "Maintainer: Test <test@example.invalid>\nDescription: isolated migration fixture\n"
    )
    config = stage / "opt/voiceflow/config.yaml"
    config.parent.mkdir(parents=True)
    config.write_text("asr:\n  endpoint: http://baseline.invalid\n")
    if version == "0.1.1":
        (stage / "DEBIAN/conffiles").write_text("/opt/voiceflow/config.yaml\n")
    output = directory / "old.deb"
    run(["dpkg-deb", "--root-owner-group", "--build", str(stage), str(output)])
    return output


def check(deb, old_version=None):
    with tempfile.TemporaryDirectory(prefix="voiceflow-isolated-dpkg-") as temp:
        directory = Path(temp)
        root = directory / "root"
        (root / "var/lib/dpkg/updates").mkdir(parents=True)
        (root / "var/lib/dpkg/status").touch()
        (root / "var/log").mkdir()
        command = ["fakeroot", "--", "dpkg", "--root=" + str(root),
                   "--log=" + str(root / "var/log/dpkg.log"),
                   "--force-script-chrootless", "--force-depends"]
        env = {**os.environ, "DEBIAN_FRONTEND": "noninteractive",
               "SUDO_USER": "this-account-must-never-be-used-in-alternate-root"}
        custom = None
        if old_version:
            old = old_package(directory, old_version)
            run([*command, "--install", str(old)], env=env)
            custom = "# Preserve comments and personal values\nasr:\n  endpoint: http://custom.invalid\n"
            if old_version == "0.1.1":
                custom += "cleanup:\n  enabled: false\n"
            path = root / "opt/voiceflow/config.yaml"
            path.write_text(custom)
            path.chmod(0o640)
        run([*command, "--install", str(deb)], env=env)
        site = root / "etc/voiceflow/config.yaml"
        assert site.exists()
        if custom:
            assert site.read_text() == custom
            assert site.stat().st_mode & 0o777 == 0o640
            backups = list((root / "var/lib/voiceflow/backups").glob("*/legacy-config.yaml"))
            assert backups and backups[0].read_text() == custom
        # Retry configure with an already-complete lifecycle: must be idempotent.
        before = site.read_bytes()
        run(["fakeroot", "--", "/usr/bin/python3",
             str(root / "var/lib/dpkg/info/voiceflow.postinst"), "configure", "1.0.0"],
            env={**env, "DPKG_ROOT": str(root)})
        assert site.read_bytes() == before
        app = root / "opt/voiceflow"
        code = (
            "from utils import load_runtime_config; from text_cleanup import TextCleaner;"
            f"c=load_runtime_config('defaults.yaml',system_path={str(site)!r},"
            "legacy_path='/nonexistent-legacy',user_path='/nonexistent-user',explicit_path=False);"
            f"assert c['cleanup']['enabled'] is {old_version != '0.1.1'};"
            "assert c['cleanup']['mode']=='rules';"
            "assert TextCleaner(c['cleanup']).smart is None"
        )
        run(["/usr/bin/python3", "-B", "-c", code], cwd=app,
            env={**os.environ, "PYTHONPATH": str(app / "python-libs")})
        print(f"PASS: {old_version or 'fresh install'} -> 1.0.0; migration/defaults/idempotence")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("deb", type=Path)
    args = parser.parse_args()
    deb = args.deb.resolve(strict=True)
    for version in (None, "0.1.0", "0.1.1"):
        check(deb, version)


if __name__ == "__main__":
    main()
