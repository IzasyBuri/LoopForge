from __future__ import annotations

import threading
import time
from collections.abc import Callable
from fractions import Fraction
from pathlib import Path
from typing import cast

import pytest
from PySide6.QtWidgets import QApplication

from loopforge.encoding import BALANCED
from loopforge.encoding_engine import EncodingResult
from loopforge.ffmpeg_service import FFmpegProgress, ProcessCancelledError
from loopforge.playlist import Playlist
from loopforge.render_context import RenderExecutionContext
from loopforge.render_tasks import RenderController, RenderJobConfig, RenderJobState


class FakeService:
    def __init__(self) -> None:
        self.started: list[str] = []
        self.gates: list[threading.Event] = []
        self.failures = 0

    def render(
        self,
        request: object,
        *,
        timeout: float | None = None,
        context: RenderExecutionContext | None = None,
    ) -> EncodingResult:
        config = cast(RenderJobConfig, request)
        gate = threading.Event()
        self.gates.append(gate)
        self.started.append(config.output.name)
        assert context is not None
        context.stage("probe")
        context.stage("encode")
        assert context.on_progress is not None
        context.on_progress(FFmpegProgress(out_time_us=500_000, speed=2.0))
        context.log(config.output.name)
        while not gate.wait(0.01):
            if context.cancelled:
                raise ProcessCancelledError("cancelled")
        if self.failures:
            self.failures -= 1
            raise RuntimeError("broken")
        return cast(EncodingResult, object())


def app() -> QApplication:
    return cast(QApplication, QApplication.instance() or QApplication([]))


def pump(predicate: Callable[[], bool], timeout: float = 3) -> None:
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        app().processEvents()
        threading.Event().wait(0.005)
    assert predicate()


def config(name: str) -> RenderJobConfig:
    return RenderJobConfig(Path("video.mp4"), Playlist(), Fraction(1), Path(name), BALANCED)


def state(controller: RenderController, job_id: str) -> RenderJobState:
    return next(value.state for value in controller.snapshots if value.id == job_id)


def test_exact_seven_states() -> None:
    assert {value.value for value in RenderJobState} == {
        "QUEUED",
        "PREPARING",
        "RUNNING",
        "PAUSED",
        "CANCELLED",
        "COMPLETED",
        "FAILED",
    }


def test_config_and_settings_snapshot_are_immutable() -> None:
    item = config("a.mp4")
    with pytest.raises(AttributeError):
        item.output = Path("b.mp4")  # type: ignore[misc]
    with pytest.raises(AttributeError):
        item.settings.quality = 1  # type: ignore[misc]


def test_add_waits_for_start_and_runs_strictly_sequential() -> None:
    service = FakeService()
    controller = RenderController(service)
    first = controller.add(config("one.mp4"))
    second = controller.add(config("two.mp4"))
    app().processEvents()
    assert service.started == [] and state(controller, first) == RenderJobState.QUEUED
    controller.start()
    pump(lambda: service.started == ["one.mp4"])
    assert state(controller, second) == RenderJobState.QUEUED
    service.gates[0].set()
    pump(lambda: service.started == ["one.mp4", "two.mp4"])
    service.gates[1].set()
    pump(lambda: state(controller, second) == RenderJobState.COMPLETED)
    controller.close()


def test_stage_progress_speed_eta_and_distinct_logs() -> None:
    service = FakeService()
    controller = RenderController(service)
    job_id = controller.add(config("one.mp4"))
    controller.start()
    pump(lambda: state(controller, job_id) == RenderJobState.RUNNING)
    snapshot = controller.snapshots[0]
    assert snapshot.progress.stage == "encode"
    assert snapshot.progress.fraction == 0.5
    assert snapshot.progress.speed == 2 and snapshot.progress.eta == 0.25
    assert snapshot.log_tail == ("one.mp4",)
    service.gates[0].set()
    pump(lambda: state(controller, job_id) == RenderJobState.COMPLETED)
    controller.close()


def test_reorder_remove_and_cancel_queued() -> None:
    controller = RenderController(FakeService())
    one = controller.add(config("one.mp4"))
    two = controller.add(config("two.mp4"))
    three = controller.add(config("three.mp4"))
    controller.reorder(three, 0)
    assert [item.id for item in controller.snapshots] == [three, one, two]
    controller.remove(one)
    controller.cancel(two)
    assert [item.id for item in controller.snapshots] == [three, two]
    assert state(controller, two) == RenderJobState.CANCELLED
    controller.close()


def test_active_cancel() -> None:
    service = FakeService()
    controller = RenderController(service)
    job_id = controller.add(config("one.mp4"))
    controller.start()
    pump(lambda: state(controller, job_id) == RenderJobState.RUNNING)
    controller.cancel(job_id)
    pump(lambda: state(controller, job_id) == RenderJobState.CANCELLED)
    controller.close()


def test_pause_then_resume_restarts_attempt() -> None:
    service = FakeService()
    controller = RenderController(service)
    job_id = controller.add(config("one.mp4"))
    controller.start()
    pump(lambda: state(controller, job_id) == RenderJobState.RUNNING)
    controller.pause(job_id)
    pump(lambda: state(controller, job_id) == RenderJobState.PAUSED)
    assert controller.snapshots[0].pause_restarts
    controller.resume(job_id)
    pump(lambda: len(service.started) == 2)
    service.gates[1].set()
    pump(lambda: state(controller, job_id) == RenderJobState.COMPLETED)
    controller.close()


def test_failure_retry_and_clear_completed() -> None:
    service = FakeService()
    service.failures = 1
    controller = RenderController(service)
    job_id = controller.add(config("one.mp4"))
    controller.start()
    pump(lambda: bool(service.gates))
    service.gates[0].set()
    pump(lambda: state(controller, job_id) == RenderJobState.FAILED)
    assert controller.snapshots[0].error == "broken"
    controller.retry(job_id)
    controller.start()
    pump(lambda: len(service.gates) == 2)
    service.gates[1].set()
    pump(lambda: state(controller, job_id) == RenderJobState.COMPLETED)
    controller.clear_completed()
    assert controller.snapshots == ()
    controller.close()


def test_close_marks_waiting_jobs_and_suppresses_completion() -> None:
    service = FakeService()
    controller = RenderController(service)
    active = controller.add(config("active.mp4"))
    waiting = controller.add(config("waiting.mp4"))
    controller.start()
    pump(lambda: state(controller, active) == RenderJobState.RUNNING)
    controller.close()
    assert state(controller, waiting) == RenderJobState.CANCELLED
