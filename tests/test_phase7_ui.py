from __future__ import annotations

import threading
import time
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from typing import Any, cast

from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtWidgets import QApplication

from loopforge.encoding import EncoderSelection
from loopforge.encoding_engine import EncodingResult
from loopforge.models import AudioStreamInfo, MediaInfo, VideoStreamInfo
from loopforge.playlist import Playlist, Track
from loopforge.playlist_widget import PlaylistPage
from loopforge.render_tasks import RenderController, UIRenderRequest
from loopforge.window import MainWindow, MediaCard


class FakeRenderController(QObject):
    succeeded = Signal(str, object)
    failed = Signal(str, str)

    def __init__(self) -> None:
        super().__init__()
        self.requests: list[UIRenderRequest] = []
        self.closed = False

    def render(self, request: UIRenderRequest) -> str:
        self.requests.append(request)
        return f"request-{len(self.requests)}"

    def close(self) -> None:
        self.closed = True


class FakeService:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.thread: threading.Thread | None = None

    def render(self, request: object, *, timeout: float | None = None) -> EncodingResult:
        self.thread = threading.current_thread()
        if self.fail:
            raise RuntimeError("broken")
        return EncodingResult(Path("done.mp4"), EncoderSelection("libx264", "cpu"), (), False)


def app() -> QApplication:
    return cast(QApplication, QApplication.instance() or QApplication([]))


def video(path: Path) -> MediaInfo:
    stream = VideoStreamInfo(0, "h264", 1920, 1080, Fraction(30), Decimal("2"))
    return MediaInfo(path, Decimal("2"), "mp4", 1, (stream,), ())


def audio(path: Path) -> MediaInfo:
    stream = AudioStreamInfo(0, "aac", 48000, 2, Decimal("2"))
    return MediaInfo(path, Decimal("2"), "mp4", 1, (), (stream,))


def track(path: Path) -> Track:
    return Track("audio", path, "Audio", "Audio", 0, Fraction(2), 48000, 2)


def ready_page(tmp_path: Path) -> tuple[PlaylistPage, FakeRenderController]:
    controller = FakeRenderController()
    page = PlaylistPage(None, None, cast(Any, controller))
    page.set_background(video(tmp_path / "background.mp4"))
    page.playlist = Playlist((track(tmp_path / "audio.wav"),))
    page.target_duration.setText("2")
    page.output_path.setText(str(tmp_path / "result.mp4"))
    page.refresh()
    return page, controller


def test_render_validation_and_accessibility(tmp_path: Path) -> None:
    app()
    page = PlaylistPage(None)
    assert (
        not page.render_button.isEnabled() and page.render_status.text() == "Rendering unavailable"
    )
    assert page.render_button.accessibleName() == "Start video render"
    controller = FakeRenderController()
    page.render_controller = cast(Any, controller)
    page._refresh_render()
    assert not page.render_button.isEnabled() and "background" in page.render_status.text()
    page.set_background(audio(tmp_path / "audio.mp4"))
    assert not page.render_button.isEnabled() and "no video" in page.background_label.text()
    page.set_background(video(tmp_path / "background.mp4"))
    assert not page.render_button.isEnabled() and "audio track" in page.render_status.text()
    page.playlist = Playlist((track(tmp_path / "audio.wav"),))
    page.refresh()
    page.target_duration.setText("invalid")
    assert not page.render_button.isEnabled() and "seconds" in page.render_status.text()
    page.target_duration.setText("2")
    page.output_path.setText(str(tmp_path / "result.mov"))
    assert not page.render_button.isEnabled() and ".mp4" in page.render_status.text()
    page.output_path.setText(str(tmp_path / "result.mp4"))
    assert page.render_button.isEnabled()
    page.close()


def test_render_snapshot_duplicate_completion_and_close(tmp_path: Path) -> None:
    app()
    page, controller = ready_page(tmp_path)
    original = page.playlist
    page.start_render()
    assert len(controller.requests) == 1
    request = controller.requests[0]
    assert request.playlist == original and request.output == tmp_path / "result.mp4"
    assert not page.render_button.isEnabled() and page.render_status.text().startswith("Rendering")
    assert "cannot be cancelled" in page.render_status.text()
    page.playlist = Playlist()
    page.start_render()
    assert len(controller.requests) == 1 and request.playlist == original
    controller.succeeded.emit("late", object())
    assert page.render_status.text().startswith("Rendering")
    page.playlist = original
    controller.failed.emit("request-1", "broken")
    assert page.render_button.isEnabled() and page.render_status.text() == "Render failed: broken"
    page.start_render()
    controller.succeeded.emit(
        "request-2",
        EncodingResult(tmp_path / "result.mp4", EncoderSelection("x", "cpu"), (), False),
    )
    assert page.render_button.isEnabled() and page.render_status.text().startswith("Rendered:")
    page.start_render()
    status = page.render_status.text()
    page.close()
    assert controller.closed
    controller.failed.emit("request-3", "late")
    assert page.render_status.text() == status


def test_main_window_keeps_video_background_for_audio_selection(tmp_path: Path) -> None:
    app()
    window = MainWindow.__new__(MainWindow)
    window.detail_display = cast(Any, type("Display", (), {"setText": lambda self, text: None})())
    page = PlaylistPage(None)
    window.playlist_page = page
    video_card = MediaCard(tmp_path / "video.mp4")
    video_card.set_ready(video(video_card.path))
    window.show_card(video_card)
    selected = page._background
    audio_card = MediaCard(tmp_path / "audio.mp4")
    audio_card.set_ready(audio(audio_card.path))
    window.show_card(audio_card)
    assert page._background is selected
    page.close()


def test_render_controller_worker_thread_success_failure_and_close() -> None:
    application = app()
    outcomes: list[str] = []

    def succeeded(_request: str, _result: object) -> None:
        outcomes.append("success")

    def failed(_request: str, _message: str) -> None:
        outcomes.append("failure")

    for fail in (False, True):
        service = FakeService(fail)
        controller = RenderController(cast(Any, service))
        outcomes.clear()
        controller.succeeded.connect(succeeded)
        controller.failed.connect(failed)
        controller.render(cast(Any, object()))
        limit = time.monotonic() + 2
        while not outcomes and time.monotonic() < limit:
            application.processEvents()
            time.sleep(0.01)
        assert outcomes == ["failure" if fail else "success"]
        assert service.thread is not threading.current_thread()
        assert QThread.currentThread() is application.thread()
        controller.close()
    service = FakeService()
    controller = RenderController(cast(Any, service))
    outcomes = []
    controller.succeeded.connect(lambda _request, _result: outcomes.append("late"))
    controller.render(cast(Any, object()))
    controller.close()
    time.sleep(0.05)
    application.processEvents()
    assert not outcomes
