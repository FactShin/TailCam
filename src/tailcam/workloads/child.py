"""Fixed worker bootstrap. No database, plugins, routing or publication authority."""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path


def _atomic_json(path: Path, value: dict) -> None:
    data = json.dumps(value, allow_nan=False).encode()
    if len(data) > 1024 * 1024:
        raise ValueError("manifest too large")
    temporary = path.with_suffix(".pending")
    with temporary.open("wb") as stream:
        stream.write(data)
    temporary.replace(path)


def guard(request_path: Path) -> int:
    """A stdlib-only guardian survives blocked native worker code.

    Work cannot begin before Windows Job Object assignment. On POSIX, every
    descendant stays in this guardian's process group. Parent EOF and deadline
    kill the group even if the heavy child holds the GIL indefinitely.
    """
    if sys.stdin.buffer.readline(8) != b"RUN\n":
        return 1
    from tailcam.workloads.process import MAX_MANIFEST_BYTES, read_json

    request = read_json(request_path, MAX_MANIFEST_BYTES)
    remaining = max(0, float(request["deadline"]) - time.time())
    deadline = time.monotonic() + remaining
    closed = threading.Event()

    def watch_parent() -> None:
        try:
            sys.stdin.buffer.read()
        finally:
            closed.set()

    threading.Thread(target=watch_parent, daemon=True).start()
    if os.name == "posix":
        signal.signal(signal.SIGTERM, lambda *_: closed.set())
    child = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "tailcam.workloads.child",
            "--actor" if request.get("persistent") else "--run",
            str(request_path),
        ],
        stdin=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
    )
    while child.poll() is None:
        if closed.wait(0.05) or time.monotonic() >= deadline:
            if os.name == "posix":
                os.killpg(os.getpgrp(), signal.SIGTERM)
                time.sleep(0.15)
                os.killpg(os.getpgrp(), signal.SIGKILL)
            child.kill()  # Windows parent owns kill-on-close descendant containment.
            child.wait(timeout=3)
            return 1
    # Retain the group leader until the parent has consumed the result. This
    # prevents a PID-reuse race when the parent terminates leftover descendants.
    _atomic_json(request_path.parent / "worker-exit.json", {"returncode": child.returncode})
    while not closed.wait(0.05) and time.monotonic() < deadline:
        pass
    if os.name == "posix":
        os.killpg(os.getpgrp(), signal.SIGTERM)
        time.sleep(0.15)
        os.killpg(os.getpgrp(), signal.SIGKILL)
    return int(child.returncode or 0)


def execute(request_path: Path) -> int:
    from tailcam.workloads.handlers import run_handler
    from tailcam.workloads.process import MAX_MANIFEST_BYTES, ExecutionError, read_json

    workspace = request_path.parent
    try:
        request = read_json(request_path, MAX_MANIFEST_BYTES)
        result = run_handler(
            request,
            workspace,
            lambda progress: _atomic_json(workspace / "progress.json", progress),
        )
    except ExecutionError as exc:
        result = {"error": exc.code}
    except (ImportError, ModuleNotFoundError):
        result = {"error": "engine_unavailable"}
    except (ValueError, TypeError, KeyError):
        result = {"error": "invalid_request"}
    except Exception:
        # URLs, filesystem paths and model-library exceptions are not wire diagnostics.
        result = {"error": "worker_failed"}
    _atomic_json(workspace / "result.json", result)
    return 0


def actor(request_path: Path) -> int:
    from tailcam.workloads.handlers import run_handler
    from tailcam.workloads.process import MAX_MANIFEST_BYTES, ExecutionError, read_json

    request = read_json(request_path, MAX_MANIFEST_BYTES)
    root = request_path.parent
    previous = ""
    while time.time() < request["deadline"]:
        pending = root / "frame-request.json"
        if not pending.exists():
            time.sleep(0.01)
            continue
        frame = read_json(pending, MAX_MANIFEST_BYTES)
        identity = str(frame.get("request_id", ""))
        if not identity or identity == previous:
            time.sleep(0.01)
            continue
        previous = identity
        try:
            result = run_handler(request, root, lambda _: None)
        except ExecutionError as exc:
            result = {"error": exc.code}
        except ImportError:
            result = {"error": "engine_unavailable"}
        except Exception:
            result = {"error": "inference_unavailable"}
        _atomic_json(root / "frame-result.json", {"request_id": identity, **result})
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--guard", type=Path)
    mode.add_argument("--run", type=Path)
    mode.add_argument("--actor", type=Path)
    args = parser.parse_args()
    if args.guard:
        return guard(args.guard)
    return actor(args.actor) if args.actor else execute(args.run)


if __name__ == "__main__":
    raise SystemExit(main())
