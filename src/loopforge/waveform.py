from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot

from .ffmpeg_service import FFmpegService

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class WaveformRequest:
    path: Path
    stream_index: int
    width: int
    height: int

    def __post_init__(self) -> None:
        if self.stream_index < 0 or self.width <= 0 or self.height <= 0:
            raise ValueError("Invalid waveform request")

    @property
    def cache_key(self) -> str:
        path = self.path.expanduser().resolve()
        stat = path.stat()
        value = (
            f"{path}\0{stat.st_size}\0{stat.st_mtime_ns}\0{self.stream_index}\0"
            f"{self.width}x{self.height}"
        )
        return hashlib.sha256(value.encode()).hexdigest()


class WaveformService:
    def __init__(self, ffmpeg: FFmpegService, cache_dir: Path) -> None:
        self.ffmpeg = ffmpeg
        self.cache_dir = cache_dir

    def render(self, request: WaveformRequest) -> Path:
        source = request.path.expanduser().resolve()
        if not source.is_file():
            raise ValueError("Waveform source must be a regular file")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        output = self.cache_dir / f"{request.cache_key}.png"
        if output.is_file():
            return output
        temporary = output.with_suffix(".tmp.png")
        temporary.unlink(missing_ok=True)
        try:
            self.ffmpeg.run(
                [
                    "-nostdin",
                    "-y",
                    "-i",
                    str(source),
                    "-map",
                    f"0:{request.stream_index}",
                    "-filter_complex",
                    f"showwavespic=s={request.width}x{request.height}",
                    "-frames:v",
                    "1",
                    str(temporary),
                ]
            )
            if not temporary.is_file():
                raise RuntimeError("FFmpeg did not create waveform")
            temporary.replace(output)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
        return output


class WaveformRenderer(Protocol):
    def render(self, request: WaveformRequest) -> Path: ...


class _WaveformSignals(QObject):
    succeeded = Signal(str, object)
    failed = Signal(str, str)


class _WaveformTask(QRunnable):
    def __init__(
        self, request_id: str, request: WaveformRequest, service: WaveformRenderer
    ) -> None:
        super().__init__()
        self.request_id = request_id
        self.request = request
        self.service = service
        self.signals = _WaveformSignals()

    @Slot()
    def run(self) -> None:
        try:
            output = self.service.render(self.request)
        except Exception as error:
            logger.exception("Waveform task failed for %s", self.request.path)
            self.signals.failed.emit(self.request_id, str(error))
        else:
            self.signals.succeeded.emit(self.request_id, output)


class WaveformController(QObject):
    succeeded = Signal(str, object)
    failed = Signal(str, str)

    def __init__(self, service: WaveformRenderer, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._service = service
        self._pool = QThreadPool(self)
        self._active: set[str] = set()
        self._closed = False

    def render(self, request: WaveformRequest) -> str:
        if self._closed:
            raise RuntimeError("Waveform controller is closed")
        request_id = uuid4().hex
        task = _WaveformTask(request_id, request, self._service)
        self._active.add(request_id)
        task.signals.succeeded.connect(self._succeeded)
        task.signals.failed.connect(self._failed)
        self._pool.start(task)
        return request_id

    @Slot(str, object)
    def _succeeded(self, request_id: str, output: Path) -> None:
        if self._closed or request_id not in self._active:
            return
        self._active.remove(request_id)
        self.succeeded.emit(request_id, output)

    @Slot(str, str)
    def _failed(self, request_id: str, message: str) -> None:
        if self._closed or request_id not in self._active:
            return
        self._active.remove(request_id)
        self.failed.emit(request_id, message)

    def close(self) -> None:
        self._closed = True
        self._active.clear()
        self._pool.clear()
