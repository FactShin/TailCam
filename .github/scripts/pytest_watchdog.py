"""Run one CI pytest phase with process-wide stack dumps and an owned-tree deadline.

The child waits for containment before importing pytest. Windows uses a private
kill-on-close Job Object; POSIX uses a new process group. No process-name search
or unscoped taskkill is used. Logs are regular files, so a blocked output reader
cannot prevent the watchdog from stopping its child.
"""

from __future__ import annotations

import argparse
import math
import os
import signal
import subprocess
import sys
import time
from pathlib import Path


class WindowsJob:
    """Contain only the Popen handle, then all of its descendants, before release."""

    def __init__(self, process: subprocess.Popen) -> None:
        import ctypes
        from ctypes import wintypes

        class Basic(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class Io(ctypes.Structure):
            _fields_ = [(name, ctypes.c_uint64) for name in (
                "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
            )]

        class Extended(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", Basic), ("IoInfo", Io),
                ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel.CreateJobObjectW.restype = wintypes.HANDLE
        kernel.SetInformationJobObject.argtypes = [
            wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
        ]
        kernel.SetInformationJobObject.restype = wintypes.BOOL
        kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        self.kernel = kernel
        self.handle = kernel.CreateJobObjectW(None, None)
        if not self.handle:
            raise OSError("Could not create the test process job")
        limits = Extended()
        limits.BasicLimitInformation.LimitFlags = 0x00002000  # KILL_ON_JOB_CLOSE; no breakaway
        handle = getattr(process, "_handle", None)  # Original handle, never a PID lookup.
        if not (
            handle is not None
            and kernel.SetInformationJobObject(
                self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)
            )
            and kernel.AssignProcessToJobObject(self.handle, int(handle))
        ):
            self.close()
            raise OSError("Could not contain the test process tree")

    def close(self) -> None:
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


def stop_owned_tree(process: subprocess.Popen, job: WindowsJob | None) -> None:
    if job is not None:
        job.close()
    elif os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except PermissionError:
            # Darwin can report EPERM for an already-empty group.
            if process.poll() is None:
                raise
    elif process.poll() is None:
        # Containment failed: this bootstrap has not received permission to run tests.
        process.kill()
    process.wait(timeout=5)


def run_pytest(args: list[str], *, timeout: float, dump_after: float, log: Path) -> int:
    log.parent.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable, "-u", str(Path(__file__).resolve()), "--child",
        "--dump-after", str(dump_after), "--", *args,
    ]
    print(f"Pytest watchdog: {timeout:g}s deadline; log: {log}", flush=True)
    with log.open("wb", buffering=0) as output:
        started = time.monotonic()
        process = subprocess.Popen(
            command, stdin=subprocess.PIPE, stdout=output, stderr=subprocess.STDOUT,
            start_new_session=os.name == "posix",
        )
        job = None
        try:
            if os.name == "nt":
                job = WindowsJob(process)
            elif os.name != "posix":
                raise OSError("Owned test process containment is unavailable on this platform")
            assert process.stdin is not None
            process.stdin.write(b"1")
            process.stdin.close()
            try:
                result = process.wait(timeout=max(0.01, timeout - (time.monotonic() - started)))
            except subprocess.TimeoutExpired:
                message = f"\nWATCHDOG: phase exceeded {timeout:g}s; stopping owned tree.\n"
                output.write(message.encode())
                result = 124
        finally:
            stop_owned_tree(process, job)
    # Replay after the child is stopped. Console backpressure cannot postpone cleanup.
    with log.open("r", encoding="utf-8", errors="replace") as recorded:
        for line in recorded:
            print(line, end="", flush=True)
    return result


def child(args: list[str], dump_after: float) -> int:
    if sys.stdin.buffer.read(1) != b"1":
        return 125
    import faulthandler

    faulthandler.enable(all_threads=True)
    # This remains armed through collection, session teardown and interpreter exit.
    # The independent parent deadline handles a blocked interpreter or native call.
    faulthandler.dump_traceback_later(dump_after, repeat=True)
    import pytest

    return int(pytest.main(["-p", "no:faulthandler", *args]))


def positive(value: str) -> float:
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise argparse.ArgumentTypeError("Use a finite positive duration")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--timeout", type=positive, default=300.0)
    parser.add_argument("--dump-after", type=positive, default=60.0)
    parser.add_argument("--log", type=Path)
    parser.add_argument("pytest_args", nargs=argparse.REMAINDER)
    options = parser.parse_args()
    args = options.pytest_args
    if args[:1] == ["--"]:
        args = args[1:]
    if options.child:
        return child(args, options.dump_after)
    if options.log is None or not args:
        parser.error("--log and pytest arguments after -- are required")
    if options.dump_after >= options.timeout:
        parser.error("--dump-after must be shorter than --timeout")
    return run_pytest(
        args, timeout=options.timeout, dump_after=options.dump_after, log=options.log,
    )


if __name__ == "__main__":
    raise SystemExit(main())
