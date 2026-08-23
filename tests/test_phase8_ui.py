from __future__ import annotations

from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from typing import Any, cast

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication

from loopforge.models import MediaInfo, VideoStreamInfo
from loopforge.playlist import Playlist, Track
from loopforge.playlist_widget import PlaylistPage
from loopforge.render_tasks import (
    RenderJobSnapshot,
    RenderJobState,
    RenderProgress,
    UIRenderRequest,
)


class Controller(QObject):
    succeeded = Signal(str, object)
    failed = Signal(str, str)
    jobs_changed = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self.requests: list[UIRenderRequest] = []
        self.started = False
        self.snapshots: tuple[RenderJobSnapshot, ...] = ()

    def add(self, request: UIRenderRequest) -> str:
        self.requests.append(request)
        return "job"

    def start(self) -> None:
        self.started = True

    def close(self) -> None:
        pass


def app() -> QApplication:
    return cast(QApplication, QApplication.instance() or QApplication([]))


def page(tmp_path: Path) -> tuple[PlaylistPage, Controller]:
    app()
    controller = Controller()
    widget = PlaylistPage(None, None, cast(Any, controller))
    video = VideoStreamInfo(0, "h264", 16, 16, Fraction(30), Decimal("1"))
    widget.set_background(MediaInfo(tmp_path / "v.mp4", Decimal("1"), "mp4", 1, (video,), ()))
    item = Track("id", tmp_path / "a.wav", "A", "A", 0, Fraction(1), 48000, 2)
    widget.playlist = Playlist((item,))
    widget.target_duration.setText("1")
    widget.output_path.setText(str(tmp_path / "out.mp4"))
    widget.refresh()
    return widget, controller


def test_queue_controls_and_accessibility(tmp_path: Path) -> None:
    widget, _ = page(tmp_path)
    assert widget.render_button.text() == "Add to Queue"
    assert widget.render_button.accessibleName() == "Start video render"
    assert widget.queue.accessibleName() == "Render queue"
    assert widget.queue_log.accessibleName() == "Selected render log"
    assert set(widget.queue_buttons) == {
        "Start Queue",
        "Remove",
        "Up",
        "Down",
        "Pause",
        "Resume",
        "Cancel",
        "Retry",
        "Clear Completed",
    }
    widget.close()


def test_enqueue_does_not_start_and_keeps_playlist_snapshot(tmp_path: Path) -> None:
    widget, controller = page(tmp_path)
    original = widget.playlist
    widget.start_render()
    widget.playlist = Playlist()
    assert not controller.started
    assert controller.requests[0].playlist == original
    assert widget.render_status.text() == "Added to queue. Select Start Queue to render."
    widget.start_queue()
    assert controller.started
    widget.close()


def test_queue_displays_every_exact_state(tmp_path: Path) -> None:
    widget, controller = page(tmp_path)
    for state in RenderJobState:
        snapshot = RenderJobSnapshot(
            state.value,
            controller.requests[0]
            if controller.requests
            else UIRenderRequest(tmp_path / "v.mp4", Playlist(), Fraction(1), tmp_path / "out.mp4"),
            state,
            RenderProgress("encode", 0.5, 2, 1.25, 3),
        )
        widget._queue_changed((snapshot,))
        row = widget.queue.topLevelItem(0)
        assert row is not None and row.text(1) == state.value
    widget.close()


def test_no_stale_cancelling_and_preparing_actions(tmp_path: Path) -> None:
    widget, _ = page(tmp_path)
    assert "CANCELLING" not in {state.value for state in RenderJobState}
    request = UIRenderRequest(tmp_path / "v.mp4", Playlist(), Fraction(1), tmp_path / "out.mp4")
    snapshot = RenderJobSnapshot("id", request, RenderJobState.PREPARING, RenderProgress())
    widget._queue_changed((snapshot,))
    row = widget.queue.topLevelItem(0)
    assert row is not None
    widget.queue.setCurrentItem(row)
    widget._refresh_queue_controls((snapshot,))
    assert widget.queue_buttons["Pause"].isEnabled()
    assert widget.queue_buttons["Cancel"].isEnabled()
    widget.close()
