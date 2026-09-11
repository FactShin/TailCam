"""Run real shell installer paths with OS/package-manager boundaries replaced."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX shell installer")


def executable(path, text):
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)


@pytest.mark.parametrize("platform", ["linux", "macos"])
def test_installer_setup_uses_shared_config_and_preserves_reruns(platform, tmp_path, isolated_env):
    script = (ROOT / f"install-{platform}.sh").read_text()
    # Exercise the actual function on the installed Python package. Never run the
    # top-level OS driver, touch real services, or use the developer's HOME.
    start = script.index("configure_node() {")
    end = script.index("\ninstall_tailcam()", start)
    venv = tmp_path / "venv"
    (venv / "bin").mkdir(parents=True)
    executable(
        venv / "bin/tailcam", f'#!/bin/sh\nexec {shlex.quote(sys.executable)} -m tailcam "$@"\n'
    )
    env = dict(
        os.environ,
        VENV_DIR=str(venv),
        PRESET="hub",
        NODE_NAME="Workshop hub",
        PORT="9123",
        NONINTERACTIVE="1",
    )
    body = "set -eu\n" + script[start:end] + "\nconfigure_node\n"
    result = subprocess.run(["bash", "-c", body], env=env, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    from tailcam import paths
    from tailcam.config import AppConfig

    assert AppConfig.load().node.roles == []
    assert AppConfig.load().server.port == 9123
    original = paths.config_file().read_bytes()
    env.update(PRESET="", NODE_NAME="", PORT="")
    result = subprocess.run(["bash", "-c", body], env=env, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert paths.config_file().read_bytes() == original
    env["PRESET"] = "typo"
    result = subprocess.run(["bash", "-c", body], env=env, text=True, capture_output=True)
    assert result.returncode != 0
    assert paths.config_file().read_bytes() == original


@pytest.mark.parametrize("platform", ["linux", "macos"])
def test_failed_config_restores_install_but_never_starts_old_service(platform, tmp_path):
    script = (ROOT / f"install-{platform}.sh").read_text()
    start = script.index("install_tailcam() {")
    end = script.index("\n# Remove a pre-rename", start)
    venv = tmp_path / "venv"
    venv.mkdir()
    (venv / "previous").write_text("keep")
    fake_python = tmp_path / "python"
    executable(
        fake_python,
        """#!/bin/sh
while [ "$#" -gt 1 ]; do shift; done
mkdir -p "$1/bin"
printf '#!/bin/sh\nexit 0\n' > "$1/bin/pip"
chmod +x "$1/bin/pip"
""",
    )
    service_calls = tmp_path / "services"
    body = (
        "set -eu\n"
        + script[start:end]
        + """
log() { :; }; warn() { :; }; err() { :; }
have() { return 1; }
configure_node() { return 1; }
systemctl() { printf '%s\n' "$*" >> "$SERVICE_CALLS"; }
launchctl() { printf '%s\n' "$*" >> "$SERVICE_CALLS"; }
install_tailcam
"""
    )
    env = dict(
        os.environ,
        VENV_DIR=str(venv),
        REF="",
        VERSION="1.9.1",
        DO_DESKTOP="0",
        PYTHON=str(fake_python),
        SERVICE_CALLS=str(service_calls),
    )
    result = subprocess.run(["bash", "-c", body], env=env, text=True, capture_output=True)
    assert result.returncode != 0
    assert (venv / "previous").read_text() == "keep"
    calls = service_calls.read_text()
    assert "--user start" not in calls and not calls.startswith("load ")


def mock_docker(tmp_path, *, capture=False, failure=False):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    executable(
        bindir / "docker",
        f"""#!{sys.executable}
import json, os, sys
args=sys.argv[1:]
with open(os.environ["CALLS"],"a") as f: f.write(json.dumps(args)+"\\n")
if args[:2]==["image","inspect"]: print("ghcr.io/factshin/tailcam@sha256:"+"a"*64)
if args and args[0]=="run" and "--rm" in args:
    print("capture={int(capture)}")
    sys.exit({int(failure)})
""",
    )
    executable(bindir / "uname", '#!/bin/sh\nprintf "Linux\\n"\n')
    # Use ordinary shell helpers but never a real Docker daemon.
    return dict(
        os.environ,
        PATH=str(bindir) + os.pathsep + os.environ["PATH"],
        CALLS=str(tmp_path / "calls"),
    )


@pytest.mark.parametrize("preset,capture", [("hub", False), ("storage", False), ("camera", True)])
def test_docker_mounts_follow_saved_roles_and_use_immutable_image(tmp_path, preset, capture):
    env = mock_docker(tmp_path, capture=capture)
    result = subprocess.run(
        ["bash", str(ROOT / "install-docker.sh"), "--preset", preset, "--no-tailscale"],
        env=env,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    calls = [json.loads(line) for line in (tmp_path / "calls").read_text().splitlines()]
    runtime = next(c for c in calls if c[:2] == ["run", "-d"])
    assert ("/dev:/dev" in runtime) is capture
    assert ("c 81:* rmw" in runtime) is capture
    assert "@sha256:" in runtime[-1]
    assert ("TAILCAM_PRESET=" + preset) in next(c for c in calls if "--rm" in c)


def test_docker_setup_failure_does_not_stop_or_delete_existing_container(tmp_path):
    env = mock_docker(tmp_path, failure=True)
    result = subprocess.run(
        ["bash", str(ROOT / "install-docker.sh"), "--preset", "hub"],
        env=env,
        text=True,
        capture_output=True,
    )
    assert result.returncode != 0
    calls = [json.loads(line) for line in (tmp_path / "calls").read_text().splitlines()]
    assert not any(c[0] in {"stop", "rm", "rename"} for c in calls)


@pytest.mark.parametrize(
    "script", ["install-linux.sh", "install-macos.sh", "install-docker.sh", "docker/entrypoint.sh"]
)
def test_shell_syntax(script):
    subprocess.run(["bash", "-n", str(ROOT / script)], check=True)
