"""Background Ollama model puller with live progress.

Downloading a vision model can take minutes, so the web UI must not block on it.
``ModelPuller`` runs a single pull on a daemon thread, streaming Ollama's progress
into a small shared state the UI polls via ``GET /api/ai/pull``.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass

import httpx

from tailcam.config import AIConfig
from tailcam.logging_setup import get_logger

log = get_logger(__name__)


@dataclass
class PullState:
    model: str = ""
    active: bool = False  # a pull is in progress
    status: str = "idle"  # idle | pulling | success | error
    completed: int = 0
    total: int = 0
    detail: str = ""
    error: str | None = None

    @property
    def percent(self) -> float:
        if self.total > 0:
            return round(100.0 * self.completed / self.total, 1)
        return 100.0 if self.status == "success" else 0.0


class ModelPuller:
    """One concurrent pull, tracked so the UI can show a progress bar."""

    def __init__(self, config: AIConfig) -> None:
        self._config = config
        self._lock = threading.Lock()
        self._state = PullState()

    def status(self) -> PullState:
        with self._lock:
            s = self._state
            return PullState(
                model=s.model, active=s.active, status=s.status,
                completed=s.completed, total=s.total, detail=s.detail, error=s.error,
            )

    def start(self, model: str) -> PullState:
        """Begin a pull if one isn't already running; returns the current state."""
        with self._lock:
            if self._state.active:
                return self._snapshot()
            self._state = PullState(model=model, active=True, status="pulling", detail="starting…")
            threading.Thread(target=self._run, args=(model,), daemon=True).start()
            return self._snapshot()

    # -- internal ----------------------------------------------------------
    def _snapshot(self) -> PullState:
        s = self._state
        return PullState(
            model=s.model, active=s.active, status=s.status,
            completed=s.completed, total=s.total, detail=s.detail, error=s.error,
        )

    def _run(self, model: str) -> None:
        base = self._config.base_url.rstrip("/")
        try:
            # A pull can legitimately run for minutes, so the read window is
            # generous — but never None: a silently-stalled socket would leave
            # the UI stuck 'active' forever. A ReadTimeout is an HTTPError,
            # caught below, which clears active.
            with httpx.stream(
                "POST", f"{base}/api/pull",
                json={"model": model, "stream": True},
                timeout=httpx.Timeout(connect=10.0, read=300.0, write=10.0, pool=10.0),
            ) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                    except ValueError:
                        continue
                    self._apply(data)
        except httpx.HTTPError as exc:
            log.debug("Ollama pull failed: %s", exc)
            with self._lock:
                self._state.status = "error"
                self._state.error = str(exc)
                self._state.active = False
            return

        with self._lock:
            if self._state.error:
                self._state.status = "error"
            else:
                self._state.status = "success"
                self._state.detail = "done"
                if self._state.total:
                    self._state.completed = self._state.total
            self._state.active = False

    def _apply(self, data: dict) -> None:
        with self._lock:
            if data.get("error"):
                self._state.error = str(data["error"])
            if isinstance(data.get("total"), int):
                self._state.total = data["total"]
            if isinstance(data.get("completed"), int):
                self._state.completed = data["completed"]
            status = data.get("status")
            if isinstance(status, str) and status:
                self._state.detail = status
