from __future__ import annotations

import logging
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot

from .encoding import BALANCED
from .encoding_engine import EncodingResult
from .hardware import HardwareDetector
from .media_probe import MediaProbeService
from .playlist import Playlist, PlaylistEngine
from .renderer import RenderRequest, VideoMusicRenderer
from .timeline import VideoLoopEngine

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class UIRenderRequest:
    media_path: Path
    playlist: Playlist
    target: Fraction
    output: Path


TaskRequest = RenderRequest | UIRenderRequest


class RenderService(Protocol):
    def render(self, request: TaskRequest, *, timeout: float | None = None) -> EncodingResult: ...


class RenderWorkflow:
    def __init__(
        self,
        probe: MediaProbeService,
        detector: HardwareDetector,
        renderer: VideoMusicRenderer,
    ) -> None:
        self.probe = probe
        self.detector = detector
        self.renderer = renderer

    def render(self, request: TaskRequest, *, timeout: float | None = None) -> EncodingResult:
        if not isinstance(request, UIRenderRequest):
            raise TypeError("UI render request required")
        info = self.probe.probe(request.media_path, count_frames=True)
        video = info.primary_video_stream
        if video is None or video.frame_rate is None:
            raise ValueError("Selected background has no usable video frame rate")
        frame_count = video.best_frame_count
        if frame_count is None:
            raise ValueError("Could not count background video frames; try another video")
        duration = Fraction(video.duration or info.duration or 0)
        if duration <= 0:
            raise ValueError("Selected background video duration is unknown")
        video_plan = VideoLoopEngine().for_target_duration(
            fps=video.frame_rate,
            source_duration=duration,
            target_duration=request.target,
            counted_frame_count=frame_count,
            stream_index=video.index,
        )
        audio_plan = PlaylistEngine().render_target(request.playlist, video_plan.duration)
        capabilities = self.detector.detect()
        return self.renderer.render(
            RenderRequest(
                request.media_path,
                request.output,
                video_plan,
                audio_plan,
                BALANCED,
                capabilities,
            ),
            timeout=timeout,
        )


class _RenderSignals(QObject):
    succeeded = Signal(str, object)
    failed = Signal(str, str)


class _RenderTask(QRunnable):
    def __init__(self, request_id: str, request: TaskRequest, service: RenderService) -> None:
        super().__init__()
        self.request_id = request_id
        self.request = request
        self.service = service
        self.signals = _RenderSignals()

    @Slot()
    def run(self) -> None:
        try:
            result = self.service.render(self.request)
        except Exception as error:
            logger.exception("Render task failed")
            self.signals.failed.emit(self.request_id, str(error))
        else:
            self.signals.succeeded.emit(self.request_id, result)


class RenderController(QObject):
    succeeded = Signal(str, object)
    failed = Signal(str, str)

    def __init__(self, service: RenderService, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._service = service
        self._pool = QThreadPool(self)
        self._active: set[str] = set()
        self._closed = False

    def render(self, request: TaskRequest) -> str:
        if self._closed:
            raise RuntimeError("Render controller is closed")
        request_id = uuid4().hex
        task = _RenderTask(request_id, request, self._service)
        self._active.add(request_id)
        task.signals.succeeded.connect(self._succeeded)
        task.signals.failed.connect(self._failed)
        self._pool.start(task)
        return request_id

    @Slot(str, object)
    def _succeeded(self, request_id: str, result: EncodingResult) -> None:
        if self._closed or request_id not in self._active:
            return
        self._active.remove(request_id)
        self.succeeded.emit(request_id, result)

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
