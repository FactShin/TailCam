"""Docker handover waits for TailCam, with every Docker call replaced locally."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX shell installer")


def run_installer(
    tmp_path,
    *,
    existing=True,
    previous_exists=False,
    readiness_exit=0,
    creation_exit=0,
    setup_failure=None,
    preflight_capture=False,
    committed_capture=False,
    installer_args=None,
):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    docker = bindir / "docker"
    docker.write_text(
        f"""#!{sys.executable}
import json, os, sys, types
args = sys.argv[1:]
with open(os.environ["CALLS"], "a") as out:
    out.write(json.dumps(args) + "\\n")
if args[:2] == ["image", "inspect"]:
    print("ghcr.io/factshin/tailcam@sha256:" + "a" * 64)
elif args and args[0] == "ps":
    if {existing!r}:
        print("tailcam")
    if {previous_exists!r}:
        print("tailcam-previous")
elif args and args[0] == "run" and "--rm" in args:
    for index, arg in enumerate(args):
        if arg == "-e":
            key, value = args[index + 1].split("=", 1)
            os.environ[key] = value
    preflight = os.environ.get("TAILCAM_SETUP_DRY_RUN") == "1"
    def configure(*, dry_run=False, **options):
        phase = "preflight" if preflight else "apply"
        with open(os.environ["SETUP_CALLS"], "a") as out:
            out.write(json.dumps(dict(phase=phase, dry_run=dry_run)) + "\\n")
        if {setup_failure!r} == phase and (preflight or not dry_run):
            raise SystemExit("mock setup failure")
        capture = {preflight_capture!r} if preflight else {committed_capture!r}
        return {{"roles": ["capture"] if capture else []}}
    setup = types.ModuleType("tailcam.setup")
    setup.configure = configure
    sys.modules["tailcam"] = types.ModuleType("tailcam")
    sys.modules["tailcam.setup"] = setup
    exec(args[args.index("-c") + 1])
elif args[:2] == ["run", "-d"]:
    print("replacement-container-id")
    sys.exit({creation_exit})
elif args and args[0] == "exec":
    sys.exit({readiness_exit})
""",
        encoding="utf-8",
    )
    docker.chmod(0o755)
    # Exercise Linux camera access decisions on every POSIX test host.
    uname = bindir / "uname"
    uname.write_text("#!/bin/sh\nprintf 'Linux\\n'\n", encoding="utf-8")
    uname.chmod(0o755)
    calls = tmp_path / "calls.jsonl"
    result = subprocess.run(
        ["bash", str(ROOT / "install-docker.sh"), *(
            installer_args if installer_args is not None else ["--preset", "hub", "--no-tailscale"]
        )],
        # No real Docker, inherited auth keys, or actual user configuration.
        env={
            "PATH": str(bindir) + os.pathsep + "/usr/bin:/bin",
            "CALLS": str(calls),
            "SETUP_CALLS": str(tmp_path / "setup_calls.jsonl"),
        },
        capture_output=True,
        text=True,
        timeout=15,
    )
    return result, [json.loads(line) for line in calls.read_text().splitlines()]


def test_previous_container_removed_only_after_bounded_readiness_succeeds(tmp_path):
    result, calls = run_installer(tmp_path)
    assert result.returncode == 0, result.stderr
    probe = [
        "exec", "tailcam", "python", "-m", "tailcam.service.readiness", "--timeout", "30",
        "--host", "127.0.0.1",
    ]
    create = next(call for call in calls if call[:2] == ["run", "-d"])
    setups = [call for call in calls if call[0] == "run" and "--rm" in call]
    assert len(setups) == 2
    assert "TAILCAM_SETUP_DRY_RUN=1" in setups[0]
    assert "TAILCAM_SETUP_DRY_RUN=0" in setups[1]
    assert (
        calls.index(setups[0])
        < calls.index(["stop", "tailcam"])
        < calls.index(["rename", "tailcam", "tailcam-previous"])
        < calls.index(setups[1])
        < calls.index(create)
        < calls.index(probe)
        < calls.index(["rm", "tailcam-previous"])
    )
    assert [
        json.loads(line) for line in (tmp_path / "setup_calls.jsonl").read_text().splitlines()
    ] == [
        {"phase": "preflight", "dry_run": True},
        {"phase": "apply", "dry_run": True},
        {"phase": "apply", "dry_run": False},
    ]
    assert "TailCam is running locally." in result.stdout
    assert ["rm", "-f", "tailcam"] not in calls


@pytest.mark.parametrize("failure", ["creation", "readiness"])
def test_failed_replacement_restores_previous_stopped_and_never_reports_success(tmp_path, failure):
    result, calls = run_installer(
        tmp_path,
        creation_exit=125 if failure == "creation" else 0,
        readiness_exit=1 if failure == "readiness" else 0,
    )
    assert result.returncode != 0
    assert ["rm", "-f", "tailcam"] in calls
    assert ["rename", "tailcam-previous", "tailcam"] in calls
    assert ["rm", "tailcam-previous"] not in calls
    assert not any(call[0] == "start" for call in calls)
    assert "TailCam is running locally." not in result.stdout
    assert "restored and left stopped" in result.stderr
    if failure == "creation":
        assert not any(call[0] == "exec" for call in calls)
    else:
        assert "did not become ready" in result.stderr


def test_failed_fresh_install_cleans_replacement_and_preserves_volumes(tmp_path):
    result, calls = run_installer(tmp_path, existing=False, readiness_exit=1)
    assert result.returncode != 0
    assert ["rm", "-f", "tailcam"] in calls
    assert not any(call[0] == "rename" for call in calls)
    assert not any(call[0] == "volume" or "--volumes" in call for call in calls)
    assert "TailCam is running locally." not in result.stdout
    assert "Persistent volumes were preserved." in result.stderr


def test_existing_recovery_container_blocks_all_configuration_changes(tmp_path):
    result, calls = run_installer(tmp_path, previous_exists=True)
    assert result.returncode != 0
    assert "earlier recovery" in result.stderr
    assert not any(call[0] in {"run", "stop", "rename", "rm", "exec"} for call in calls)
    assert not (tmp_path / "setup_calls.jsonl").exists()


@pytest.mark.parametrize("failure", ["preflight", "apply"])
def test_configuration_failure_never_starts_replacement(tmp_path, failure):
    result, calls = run_installer(tmp_path, setup_failure=failure)
    assert result.returncode != 0
    assert not any(call[:2] == ["run", "-d"] or call[0] == "exec" for call in calls)
    assert ["rm", "tailcam-previous"] not in calls
    assert "TailCam is running locally." not in result.stdout
    if failure == "preflight":
        assert not any(call[0] in {"stop", "rename", "rm"} for call in calls)
        assert "existing container was left untouched" in result.stderr
    else:
        assert ["stop", "tailcam"] in calls
        assert ["rename", "tailcam-previous", "tailcam"] in calls
        assert not any(call[0] == "start" for call in calls)
        assert "restored and left stopped" in result.stderr
        assert "Persistent volumes were preserved." in result.stderr


@pytest.mark.parametrize("committed_capture", [False, True])
def test_camera_access_uses_final_saved_roles_after_shutdown(tmp_path, committed_capture):
    result, calls = run_installer(
        tmp_path,
        preflight_capture=not committed_capture,
        committed_capture=committed_capture,
        installer_args=["--no-tailscale"],
    )
    assert result.returncode == 0, result.stderr
    create = next(call for call in calls if call[:2] == ["run", "-d"])
    assert ("/dev:/dev" in create) == committed_capture
    assert ("c 81:* rmw" in create) == committed_capture


def test_changed_roles_reject_explicit_camera_device_before_commit(tmp_path):
    result, calls = run_installer(
        tmp_path,
        preflight_capture=True,
        committed_capture=False,
        installer_args=["--no-tailscale", "--device", "/dev/video0"],
    )
    assert result.returncode != 0
    assert "Camera devices require the capture role" in result.stderr
    assert ["rename", "tailcam-previous", "tailcam"] in calls
    assert not any(call[:2] == ["run", "-d"] for call in calls)
    assert [
        json.loads(line) for line in (tmp_path / "setup_calls.jsonl").read_text().splitlines()
    ] == [
        {"phase": "preflight", "dry_run": True},
        {"phase": "apply", "dry_run": True},
    ]
