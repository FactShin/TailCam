"""The CI watchdog must diagnose every pytest phase and stop only its own tree."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / ".github/scripts/pytest_watchdog.py"


def invoke(tmp_path, source, *, timeout=6):
    test_file = tmp_path / "test_isolated.py"
    test_file.write_text(source, encoding="utf-8")
    config = tmp_path / "pytest.ini"
    config.write_text("[pytest]\n", encoding="utf-8")
    log = tmp_path / "phase.log"
    result = subprocess.run(
        [
            sys.executable, str(SCRIPT), "--timeout", str(timeout), "--dump-after", "1",
            "--log", str(log), "--", "-vv", "-s", "-c", str(config),
            "--confcutdir", str(tmp_path), str(test_file),
        ],
        cwd=tmp_path,
        env={**os.environ, "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"},
        capture_output=True,
        text=True,
        timeout=20,
    )
    return result, log.read_text(encoding="utf-8")


@pytest.mark.parametrize("passes,code", [(True, 0), (False, 1)])
def test_preserves_pytest_exit_code_and_diagnostic_log(tmp_path, passes, code):
    result, log = invoke(
        tmp_path,
        f"def test_result():\n    print('diagnostic marker', flush=True)\n    assert {passes}\n",
    )
    assert result.returncode == code, result.stdout + result.stderr
    assert "diagnostic marker" in log and "diagnostic marker" in result.stdout
    assert "WATCHDOG: phase exceeded" not in log


def test_collection_timeout_stops_owned_descendant_and_preserves_unrelated_process(tmp_path):
    heartbeat = tmp_path / "descendant-heartbeat"
    descendant = (
        "import time; from pathlib import Path; "
        f"path=Path({str(heartbeat)!r}); until=time.monotonic()+15\n"
        "while time.monotonic()<until:\n"
        "    with path.open('a') as stream: stream.write('alive\\n')\n"
        "    time.sleep(0.05)\n"
    )
    source = (
        "import subprocess, sys, time\n"
        "def hang_collection():\n"
        f"    subprocess.Popen([sys.executable, '-c', {descendant!r}])\n"
        "    print('collection entered', flush=True)\n"
        "    time.sleep(60)\n"
        "hang_collection()\n"
    )
    unrelated = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    started = time.monotonic()
    try:
        result, log = invoke(tmp_path, source)
        assert result.returncode == 124, result.stdout + result.stderr
        assert time.monotonic() - started < 16
        assert "collection entered" in log and "hang_collection" in log and "Timeout" in log
        assert "WATCHDOG: phase exceeded" in result.stdout
        assert unrelated.poll() is None
        first = heartbeat.read_bytes()
        assert first
        time.sleep(0.3)
        assert heartbeat.read_bytes() == first, "Owned descendant survived watchdog cleanup"
    finally:
        unrelated.terminate()
        unrelated.wait(timeout=5)


def test_periodic_snapshots_do_not_interrupt_a_passing_phase(tmp_path):
    result, log = invoke(
        tmp_path,
        "import time\n"
        "def test_slow_success():\n"
        "    time.sleep(2.2)\n",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert log.count("Timeout diagnostic") >= 2
    assert "test_slow_success" in log
    assert "WATCHDOG: phase exceeded" not in log


@pytest.mark.parametrize("phase", ["session", "exit"])
def test_timeout_and_stacks_cover_session_cleanup_and_interpreter_exit(tmp_path, phase):
    if phase == "session":
        (tmp_path / "conftest.py").write_text(
            "import time\n"
            "def pytest_sessionfinish(session, exitstatus):\n"
            "    print('session cleanup entered', flush=True)\n"
            "    time.sleep(60)\n",
            encoding="utf-8",
        )
        source = "def test_done():\n    pass\n"
        frame = "pytest_sessionfinish"
    else:
        source = (
            "import atexit, time\n"
            "def hang_at_exit():\n"
            "    print('interpreter exit entered', flush=True)\n"
            "    time.sleep(60)\n"
            "atexit.register(hang_at_exit)\n"
            "def test_done():\n    pass\n"
        )
        frame = "hang_at_exit"
    result, log = invoke(tmp_path, source)
    assert result.returncode == 124, result.stdout + result.stderr
    assert frame in log and "Timeout" in log
    assert "WATCHDOG: phase exceeded" in result.stdout


def test_windows_phases_keep_required_job_name_and_use_watchdog():
    import yaml

    workflow = yaml.load(
        (SCRIPT.parents[1] / "workflows/tests.yml").read_text(), Loader=yaml.BaseLoader,
    )
    matrix_job = workflow["jobs"]["windows-phases"]
    assert matrix_job["strategy"]["fail-fast"] == "false"
    phases = matrix_job["strategy"]["matrix"]["phase"]
    commands = " ".join(phase["tests"] for phase in phases).split()
    for required in (
        "tests/test_windows.py", "tests/test_api.py", "tests/test_node_lifecycle.py",
        "tests/test_storage_contracts.py", "tests/test_artifact_transfers.py",
        "tests/test_storage_http.py", "tests/test_storage_processes.py",
        "tests/test_training_export_security.py", "tests/test_pytest_watchdog.py",
        "tests/test_workload_execution.py", "tests/test_artifact_pins.py",
        "tests/test_training_supervisor.py", "tests/test_legacy_admin_security.py",
        "tests/test_live_worker_readiness.py",
    ):
        assert required in commands, f"Windows coverage omitted {required}"
    assert len(commands) == len(set(commands)), "Windows phases must not duplicate test files"
    assert len({phase["id"] for phase in phases}) == len(phases)
    steps = matrix_job["steps"]
    assert any("pytest_watchdog.py --timeout 300 --dump-after 60" in step.get("run", "")
               and "--log artifacts/" in step["run"] for step in steps)
    assert any(step.get("uses") == "actions/upload-artifact@v4"
               and step.get("if") == "always()" for step in steps)
    gate = workflow["jobs"]["test-windows"]
    assert gate["needs"] == "windows-phases" and "always()" in gate["if"]
    check = gate["steps"][0]
    assert check["env"]["WINDOWS_PHASES_RESULT"] == "${{ needs.windows-phases.result }}"
    assert check["run"] == 'test "$WINDOWS_PHASES_RESULT" = success'
