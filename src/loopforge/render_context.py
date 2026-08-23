from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field

from .ffmpeg_service import ActiveProcess, FFmpegProgress, ProcessCancelledError


@dataclass(slots=True)
class RenderExecutionContext:
    on_stage: Callable[[str], None] | None = None
    on_progress: Callable[[FFmpegProgress], None] | None = None
    on_log: Callable[[str], None] | None = None
    _cancelled: threading.Event = field(default_factory=threading.Event, init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False)
    _process: ActiveProcess | None = field(default=None, init=False)

    @property
    def cancelled(self) -> bool:
        return self._cancelled.is_set()

    def cancel(self) -> None:
        self._cancelled.set()
        with self._lock:
            process = self._process
        if process is not None:
            process.cancel()

    def check_cancelled(self) -> None:
        if self.cancelled:
            raise ProcessCancelledError("Render cancelled")

    def process_started(self, process: ActiveProcess) -> None:
        with self._lock:
            self._process = process
        if self.cancelled:
            process.cancel()

    def stage(self, value: str) -> None:
        self.check_cancelled()
        if self.on_stage is not None:
            self.on_stage(value)

    def log(self, value: str) -> None:
        if self.on_log is not None:
            self.on_log(value)
