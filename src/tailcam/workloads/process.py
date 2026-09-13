"""Owned child processes. These limits never target the camera/API process.

The worker environment redirects dependency caches; this is not an OS disk
quota. Requests for hard memory or aggregate filesystem limits are refused.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

MAX_MANIFEST_BYTES = 1024 * 1024
MAX_PROGRESS_BYTES = 16384


class ExecutionError(RuntimeError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code, self.detail = code, detail


def child_environment(workspace: Path, cpu_threads: int = 1) -> dict[str, str]:
    """Use only runtime settings, never inherited peer/cloud credentials."""
    inherited = (
        "PATH",
        "SystemRoot",
        "SYSTEMROOT",
        "WINDIR",
        "COMSPEC",
        "PATHEXT",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "LD_LIBRARY_PATH",
        "DYLD_LIBRARY_PATH",
        "TAILCAM_WORKER_OFFLINE",
    )
    env = {key: os.environ[key] for key in inherited if key in os.environ}
    cache = workspace / "cache"
    cache.mkdir(exist_ok=True)
    temp = workspace / "tmp"
    temp.mkdir(exist_ok=True)
    for key in (
        "HOME",
        "USERPROFILE",
        "XDG_CACHE_HOME",
        "XDG_CONFIG_HOME",
        "HF_HOME",
        "HF_HUB_CACHE",
        "HUGGINGFACE_HUB_CACHE",
        "TRANSFORMERS_CACHE",
        "HF_MODULES_CACHE",
        "TORCH_HOME",
        "YOLO_CONFIG_DIR",
        "TRITON_CACHE_DIR",
        "NUMBA_CACHE_DIR",
        "CUDA_CACHE_PATH",
        "MPLCONFIGDIR",
        "APPDATA",
        "LOCALAPPDATA",
    ):
        env[key] = str(cache / key.lower())
    for key in ("TMPDIR", "TEMP", "TMP"):
        env[key] = str(temp)
    env.update(
        {
            "PYTHONPATH": str(Path(__file__).resolve().parents[2]),
            "PYTHONUNBUFFERED": "1",
            "PYTHONNOUSERSITE": "1",
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "HF_HUB_DISABLE_TELEMETRY": "1",
            "DO_NOT_TRACK": "1",
            "OMP_NUM_THREADS": str(cpu_threads),
            "MKL_NUM_THREADS": str(cpu_threads),
        }
    )
    return env


class _WindowsJob:
    """Kill-on-close Job Object; assignment precedes the bootstrap handshake."""

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
            _fields_ = [
                (name, ctypes.c_uint64)
                for name in (
                    "ReadOperationCount",
                    "WriteOperationCount",
                    "OtherOperationCount",
                    "ReadTransferCount",
                    "WriteTransferCount",
                    "OtherTransferCount",
                )
            ]

        class Extended(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", Basic),
                ("IoInfo", Io),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
        kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel.CreateJobObjectW.restype = wintypes.HANDLE
        kernel.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        kernel.SetInformationJobObject.restype = wintypes.BOOL
        kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        self._kernel = kernel
        self._handle = kernel.CreateJobObjectW(None, None)
        if not self._handle:
            raise ExecutionError("containment_unavailable", "Cannot create worker process job.")
        limits = Extended()
        limits.BasicLimitInformation.LimitFlags = 0x00002000  # KILL_ON_JOB_CLOSE, no breakaway
        # Popen retains the original process HANDLE on CPython/Windows; no PID lookup/reuse race.
        process_handle = getattr(process, "_handle", None)
        if not (
            process_handle is not None
            and kernel.SetInformationJobObject(
                self._handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)
            )
            and kernel.AssignProcessToJobObject(self._handle, int(process_handle))
        ):
            self.close()
            raise ExecutionError("containment_unavailable", "Cannot contain worker process tree.")

    def close(self) -> None:
        if self._handle:
            self._kernel.CloseHandle(self._handle)
            self._handle = None


def _terminate(process: subprocess.Popen, windows_job: _WindowsJob | None) -> None:
    if windows_job is not None:
        windows_job.close()
    elif os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        time.sleep(0.1)
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except PermissionError:
            # Darwin may report EPERM for an already-empty/reaped group. Any
            # still-running owned leader must remain a visible cleanup failure.
            if process.poll() is None:
                raise ExecutionError(
                    "termination_failed", "Worker group could not be stopped."
                ) from None
    elif process.poll() is None:
        process.kill()
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired as exc:
        raise ExecutionError("termination_failed", "Worker did not terminate.") from exc


def read_json(path: Path, limit: int) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ExecutionError("invalid_manifest", "Worker result is missing or unsafe.")
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ExecutionError("invalid_manifest", "Worker result exceeds its bound.")
    try:
        result = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ExecutionError("invalid_manifest", "Worker result is not valid JSON.") from exc
    if not isinstance(result, dict):
        raise ExecutionError("invalid_manifest", "Worker result must be an object.")
    return result


class ProcessRunner:
    """Run the fixed installed bootstrap in an existing admitted workspace."""

    def run(
        self,
        request: dict[str, Any],
        workspace: Path,
        *,
        deadline: float,
        cancel: threading.Event | None = None,
        on_progress: Callable[[dict[str, Any]], None] | None = None,
        check_workspace: Callable[[], Any] | None = None,
    ) -> dict[str, Any]:
        if os.name not in {"posix", "nt"}:
            raise ExecutionError("containment_unavailable", "Platform cannot contain workers.")
        if deadline <= time.time():
            raise ExecutionError("deadline_exceeded", "Job deadline has elapsed.")
        if workspace.is_symlink() or not workspace.is_dir():
            raise ExecutionError("invalid_workspace", "An admitted workspace is required.")
        workspace = workspace.resolve()
        request_path = workspace / "request.json"
        payload = json.dumps({**request, "deadline": deadline}, allow_nan=False).encode()
        if len(payload) > MAX_MANIFEST_BYTES:
            raise ExecutionError("invalid_request", "Worker request exceeds its bound.")
        with request_path.open("xb") as stream:
            stream.write(payload)
        result_path = workspace / "result.json"
        command = [sys.executable, "-m", "tailcam.workloads.child", "--guard", str(request_path)]
        kwargs: dict[str, Any] = (
            {"start_new_session": True}
            if os.name == "posix"
            else {
                "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0),
            }
        )
        process = subprocess.Popen(
            command,
            cwd=workspace,
            env=child_environment(workspace, int(request.get("cpu_threads", 1))),
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            **kwargs,
        )
        windows_job = None
        try:
            if os.name == "nt":
                windows_job = _WindowsJob(process)
            assert process.stdin is not None
            process.stdin.write(b"RUN\n")
            process.stdin.flush()
            last_progress: dict[str, Any] = {}
            monotonic_deadline = time.monotonic() + max(0, deadline - time.time())
            returncode = None
            while process.poll() is None:
                if cancel is not None and cancel.is_set():
                    raise ExecutionError("cancelled", "Worker was cancelled.")
                if time.monotonic() >= monotonic_deadline:
                    raise ExecutionError("deadline_exceeded", "Worker exceeded its deadline.")
                if check_workspace is not None:
                    check_workspace()
                exit_path = workspace / "worker-exit.json"
                if exit_path.exists():
                    returncode = read_json(exit_path, MAX_PROGRESS_BYTES).get("returncode")
                    break
                progress_path = workspace / "progress.json"
                if on_progress is not None and progress_path.exists():
                    progress = read_json(progress_path, MAX_PROGRESS_BYTES)
                    if progress != last_progress:
                        on_progress(progress)
                        last_progress = progress
                time.sleep(0.05)
            if cancel is not None and cancel.is_set():
                raise ExecutionError("cancelled", "Worker was cancelled.")
            if time.time() >= deadline:
                raise ExecutionError("deadline_exceeded", "Worker exceeded its deadline.")
            if (returncode if returncode is not None else process.returncode) != 0:
                raise ExecutionError("worker_failed", "Worker exited before completing the job.")
            result = read_json(result_path, MAX_MANIFEST_BYTES)
            if result.get("error"):
                code = result["error"]
                if code not in {
                    "invalid_request",
                    "input_unavailable",
                    "engine_unavailable",
                    "inference_unavailable",
                    "worker_failed",
                    "unsupported_task",
                    "unsupported_provider",
                }:
                    code = "worker_failed"
                raise ExecutionError(code, "Worker could not complete the requested operation.")
            return result
        finally:
            if process.stdin is not None:
                try:
                    process.stdin.close()
                except OSError:
                    pass
            _terminate(process, windows_job)


class ProcessActor:
    """One isolated model session; each frame has an independent hard deadline."""

    def __init__(self, request: dict[str, Any], workspace: Path, *, deadline: float) -> None:
        self.max_bytes = int(request["workspace_bytes"])
        self.path = workspace.resolve()
        self.deadline = deadline
        self._closed = False
        self._lock = threading.Lock()
        request = {**request, "persistent": True, "deadline": deadline}
        payload = json.dumps(request, allow_nan=False).encode()
        if len(payload) > MAX_MANIFEST_BYTES:
            raise ExecutionError("invalid_request", "Live request exceeds its bound.")
        request_path = self.path / "request.json"
        with request_path.open("xb") as stream:
            stream.write(payload)
        self.process = subprocess.Popen(
            [sys.executable, "-m", "tailcam.workloads.child", "--guard", str(request_path)],
            cwd=self.path,
            env=child_environment(self.path, int(request.get("cpu_threads", 1))),
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            **(
                {"start_new_session": True}
                if os.name == "posix"
                else {
                    "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0),
                }
            ),
        )
        self._job = None
        try:
            if os.name == "nt":
                self._job = _WindowsJob(self.process)
            assert self.process.stdin is not None
            self.process.stdin.write(b"RUN\n")
            self.process.stdin.flush()
        except Exception:
            self.close()
            raise

    def infer(
        self,
        request_id: str,
        jpeg: bytes,
        *,
        deadline: float,
        check_workspace: Callable[[], Any] | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            if self._closed or time.time() >= self.deadline:
                raise ExecutionError("session_expired", "Live worker session expired.")
            if not jpeg or len(jpeg) > 12 * 1024**2:
                raise ExecutionError("invalid_request", "Live image exceeds its byte bound.")
            if check_workspace and check_workspace() + len(jpeg) + 1024 > self.max_bytes:
                raise ExecutionError("workspace_full", "Live frame exceeds its scratch budget.")
            pending = self.path / "frame.pending"
            pending.write_bytes(jpeg)
            pending.replace(self.path / "frame.jpg")
            metadata = self.path / "frame-request.pending"
            metadata.write_text(json.dumps({"request_id": request_id}), encoding="utf-8")
            metadata.replace(self.path / "frame-request.json")
            limit = time.monotonic() + max(0, min(deadline, self.deadline) - time.time())
            try:
                while time.monotonic() < limit:
                    if self.process.poll() is not None:
                        raise ExecutionError("worker_failed", "Live worker exited.")
                    if check_workspace:
                        check_workspace()
                    result_path = self.path / "frame-result.json"
                    if result_path.exists():
                        result = read_json(result_path, MAX_MANIFEST_BYTES)
                        if result.get("request_id") == request_id:
                            if result.get("error"):
                                raise ExecutionError(
                                    "inference_unavailable", "The selected engine could not answer."
                                )
                            return result["result"]
                    time.sleep(0.01)
                raise ExecutionError("deadline_exceeded", "Live inference exceeded its deadline.")
            except Exception:
                self.close()
                raise

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self.process.stdin:
            try:
                self.process.stdin.close()
            except OSError:
                pass
        _terminate(self.process, self._job)
