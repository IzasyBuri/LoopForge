from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, replace
from enum import StrEnum
from fractions import Fraction
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot

from .encoding import BALANCED, EncodingSettings
from .encoding_engine import EncodingResult
from .ffmpeg_service import FFmpegProgress, ProcessCancelledError
from .hardware import HardwareDetector
from .media_probe import MediaProbeService
from .playlist import Playlist, PlaylistEngine
from .render_context import RenderExecutionContext
from .renderer import RenderRequest, VideoMusicRenderer
from .timeline import VideoLoopEngine

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RenderJobConfig:
    media_path: Path
    playlist: Playlist
    target: Fraction
    output: Path
    settings: EncodingSettings = BALANCED
    overwrite: bool = False


UIRenderRequest = RenderJobConfig
TaskRequest = RenderRequest | RenderJobConfig


class RenderJobState(StrEnum):
    QUEUED = "QUEUED"
    PREPARING = "PREPARING"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    CANCELLED = "CANCELLED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


@dataclass(frozen=True, slots=True)
class RenderProgress:
    stage: str = ""
    fraction: float | None = None
    elapsed: float = 0.0
    speed: float | None = None
    eta: float | None = None


@dataclass(frozen=True, slots=True)
class RenderJobSnapshot:
    id: str
    config: TaskRequest
    state: RenderJobState
    progress: RenderProgress
    error: str = ""
    log_tail: tuple[str, ...] = ()
    pause_restarts: bool = False


class RenderService(Protocol):
    def render(
        self,
        request: TaskRequest,
        *,
        timeout: float | None = None,
        context: RenderExecutionContext | None = None,
    ) -> EncodingResult: ...


class RenderWorkflow:
    def __init__(
        self, probe: MediaProbeService, detector: HardwareDetector, renderer: VideoMusicRenderer
    ) -> None:
        self.probe = probe
        self.detector = detector
        self.renderer = renderer

    def render(
        self,
        request: TaskRequest,
        *,
        timeout: float | None = None,
        context: RenderExecutionContext | None = None,
    ) -> EncodingResult:
        if not isinstance(request, RenderJobConfig):
            raise TypeError("UI render request required")
        if context:
            context.stage("probe")
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
        if context:
            context.check_cancelled()
            context.stage("hardware")
        capabilities = self.detector.detect()
        return self.renderer.render(
            RenderRequest(
                request.media_path,
                request.output,
                video_plan,
                audio_plan,
                request.settings,
                capabilities,
                request.overwrite,
            ),
            timeout=timeout,
            context=context,
        )


@dataclass(slots=True)
class _Job:
    snapshot: RenderJobSnapshot
    context: RenderExecutionContext | None = None
    started: float = 0.0
    cancel_target: RenderJobState | None = None


class _RenderSignals(QObject):
    finished = Signal(str, object, str)
    stage = Signal(str, str)
    progress = Signal(str, object)
    log = Signal(str, str)


class _RenderTask(QRunnable):
    def __init__(
        self,
        job_id: str,
        request: TaskRequest,
        service: RenderService,
        context: RenderExecutionContext,
    ) -> None:
        super().__init__()
        self.job_id = job_id
        self.request = request
        self.service = service
        self.context = context
        self.signals = _RenderSignals()

    @Slot()
    def run(self) -> None:
        try:
            try:
                result = self.service.render(self.request, context=self.context)
            except TypeError as error:
                if "context" not in str(error):
                    raise
                result = self.service.render(self.request)
        except ProcessCancelledError as error:
            self.signals.finished.emit(self.job_id, None, str(error))
        except Exception as error:
            logger.exception("Render task failed")
            self.signals.finished.emit(self.job_id, None, str(error))
        else:
            self.signals.finished.emit(self.job_id, result, "")


class RenderController(QObject):
    succeeded = Signal(str, object)
    failed = Signal(str, str)
    jobs_changed = Signal(object)

    def __init__(
        self, service: RenderService, parent: QObject | None = None, log_dir: Path | None = None
    ) -> None:
        super().__init__(parent)
        self._service = service
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(1)
        self._jobs: list[_Job] = []
        self._closed = False
        self._running = False
        self._log_dir = log_dir
        self._lock = threading.RLock()
        if log_dir:
            log_dir.mkdir(parents=True, exist_ok=True)

    @property
    def snapshots(self) -> tuple[RenderJobSnapshot, ...]:
        with self._lock:
            return tuple(job.snapshot for job in self._jobs)

    def add(self, config: RenderJobConfig) -> str:
        if self._closed:
            raise RuntimeError("Render controller is closed")
        job_id = uuid4().hex
        snapshot = RenderJobSnapshot(job_id, config, RenderJobState.QUEUED, RenderProgress())
        self._jobs.append(_Job(snapshot))
        self._emit()
        return job_id

    def render(self, request: TaskRequest) -> str:
        if not isinstance(request, RenderJobConfig):
            if self._closed:
                raise RuntimeError("Render controller is closed")
            job_id = uuid4().hex
            self._jobs.append(
                _Job(RenderJobSnapshot(job_id, request, RenderJobState.QUEUED, RenderProgress()))
            )
        else:
            job_id = self.add(request)
        self.start()
        return job_id

    def remove(self, job_id: str) -> None:
        self._jobs = [
            job
            for job in self._jobs
            if not (
                job.snapshot.id == job_id
                and job.snapshot.state in {RenderJobState.QUEUED, RenderJobState.PAUSED}
            )
        ]
        self._emit()

    def reorder(self, job_id: str, index: int) -> None:
        job = self._find(job_id)
        if job.snapshot.state not in {RenderJobState.QUEUED, RenderJobState.PAUSED}:
            return
        self._jobs.remove(job)
        self._jobs.insert(max(0, min(index, len(self._jobs))), job)
        self._emit()

    def start(self) -> None:
        if self._closed:
            raise RuntimeError("Render controller is closed")
        self._running = True
        self._schedule()

    def pause(self, job_id: str) -> None:
        job = self._find(job_id)
        if job.snapshot.state in {RenderJobState.PREPARING, RenderJobState.RUNNING}:
            job.cancel_target = RenderJobState.PAUSED
            job.snapshot = replace(job.snapshot, pause_restarts=True)
            self._append_log(job, "Pause requested; resume restarts render from beginning.")
            assert job.context
            job.context.cancel()
            self._emit()

    def resume(self, job_id: str) -> None:
        job = self._find(job_id)
        if job.snapshot.state == RenderJobState.PAUSED:
            job.snapshot = replace(
                job.snapshot, state=RenderJobState.QUEUED, progress=RenderProgress(), error=""
            )
            self._append_log(job, "Resumed; render restarting from beginning.")
            self._running = True
            self._emit()
            self._schedule()

    def cancel(self, job_id: str) -> None:
        job = self._find(job_id)
        if job.snapshot.state in {RenderJobState.QUEUED, RenderJobState.PAUSED}:
            job.snapshot = replace(job.snapshot, state=RenderJobState.CANCELLED)
        elif job.snapshot.state in {RenderJobState.PREPARING, RenderJobState.RUNNING}:
            job.cancel_target = RenderJobState.CANCELLED
            if job.context:
                job.context.cancel()
        self._emit()

    def retry(self, job_id: str) -> None:
        job = self._find(job_id)
        if job.snapshot.state in {RenderJobState.FAILED, RenderJobState.CANCELLED}:
            job.snapshot = replace(
                job.snapshot, state=RenderJobState.QUEUED, progress=RenderProgress(), error=""
            )
            self._emit()

    def clear_completed(self) -> None:
        self._jobs = [job for job in self._jobs if job.snapshot.state != RenderJobState.COMPLETED]
        self._emit()

    def _schedule(self) -> None:
        if (
            self._closed
            or not self._running
            or any(
                job.snapshot.state in {RenderJobState.PREPARING, RenderJobState.RUNNING}
                for job in self._jobs
            )
        ):
            return
        job = next(
            (item for item in self._jobs if item.snapshot.state == RenderJobState.QUEUED), None
        )
        if job is None:
            self._running = False
            return
        job.started = time.monotonic()
        signals = _RenderSignals()
        signals.stage.connect(self._update_stage)
        signals.progress.connect(self._update_progress)
        signals.log.connect(self._log)
        context = RenderExecutionContext(
            on_stage=lambda stage: signals.stage.emit(job.snapshot.id, stage),
            on_progress=lambda progress: signals.progress.emit(job.snapshot.id, progress),
            on_log=lambda line: signals.log.emit(job.snapshot.id, line),
        )
        job.context = context
        job.cancel_target = None
        job.snapshot = replace(job.snapshot, state=RenderJobState.PREPARING)
        task = _RenderTask(job.snapshot.id, job.snapshot.config, self._service, context)
        task.signals.finished.connect(self._finished)
        self._emit()
        self._pool.start(task)

    @Slot(str, str)
    def _update_stage(self, job_id: str, stage: str) -> None:
        job = self._find(job_id)
        elapsed = time.monotonic() - job.started
        state = (
            RenderJobState.PREPARING if stage in {"probe", "hardware"} else RenderJobState.RUNNING
        )
        job.snapshot = replace(
            job.snapshot, state=state, progress=RenderProgress(stage, None, elapsed)
        )
        self._emit()

    @Slot(str, object)
    def _update_progress(self, job_id: str, value: FFmpegProgress) -> None:
        job = self._find(job_id)
        config = job.snapshot.config
        duration = float(
            config.target if isinstance(config, RenderJobConfig) else config.video_plan.duration
        )
        fraction = (
            None if value.out_time_seconds is None else min(1.0, value.out_time_seconds / duration)
        )
        eta = None
        if fraction and value.speed and value.speed > 0:
            eta = max(0.0, duration - (value.out_time_seconds or 0)) / value.speed
        job.snapshot = replace(
            job.snapshot,
            progress=RenderProgress(
                job.snapshot.progress.stage,
                fraction,
                time.monotonic() - job.started,
                value.speed,
                eta,
            ),
        )
        self._emit()

    @Slot(str, str)
    def _log(self, job_id: str, line: str) -> None:
        self._append_log(self._find(job_id), line)
        self._emit()

    def _append_log(self, job: _Job, line: str) -> None:
        tail = (*job.snapshot.log_tail, line)[-200:]
        job.snapshot = replace(job.snapshot, log_tail=tail)
        if self._log_dir:
            with (self._log_dir / f"{job.snapshot.id}.log").open("a", encoding="utf-8") as stream:
                stream.write(f"{line}\n")

    @Slot(str, object, str)
    def _finished(self, job_id: str, result: EncodingResult | None, error: str) -> None:
        if self._closed:
            return
        job = self._find(job_id)
        target = job.cancel_target
        if target is not None or (job.context and job.context.cancelled):
            state = target or RenderJobState.CANCELLED
            job.snapshot = replace(job.snapshot, state=state, error="")
        elif error:
            job.snapshot = replace(job.snapshot, state=RenderJobState.FAILED, error=error)
            self.failed.emit(job_id, error)
        else:
            job.snapshot = replace(
                job.snapshot,
                state=RenderJobState.COMPLETED,
                progress=replace(
                    job.snapshot.progress, fraction=1.0, elapsed=time.monotonic() - job.started
                ),
            )
            self.succeeded.emit(job_id, result)
        job.context = None
        self._emit()
        self._schedule()

    def _find(self, job_id: str) -> _Job:
        return next(job for job in self._jobs if job.snapshot.id == job_id)

    def _emit(self) -> None:
        if not self._closed:
            self.jobs_changed.emit(self.snapshots)

    def close(self, timeout_ms: int = 2000) -> None:
        self._closed = True
        self._running = False
        for job in self._jobs:
            if job.context:
                job.context.cancel()
            if job.snapshot.state in {RenderJobState.QUEUED, RenderJobState.PAUSED}:
                job.snapshot = replace(job.snapshot, state=RenderJobState.CANCELLED)
        self._pool.clear()
        self._pool.waitForDone(timeout_ms)
