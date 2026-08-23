from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from typing import cast

from PySide6.QtCore import QThread, QUrl
from PySide6.QtWidgets import (
    QApplication,
    QListWidget,
    QPushButton,
)

from loopforge.config import Settings
from loopforge.lifecycle import Runtime
from loopforge.models import AudioStreamInfo, MediaInfo, VideoStreamInfo
from loopforge.probe_tasks import ProbeController, extract_local_files
from loopforge.window import MainWindow, MediaCard


def application() -> QApplication:
    instance = QApplication.instance()
    return QApplication([]) if instance is None else cast(QApplication, instance)


def pump_until(predicate: Callable[[], bool], timeout: float = 2.0) -> None:
    app = application()
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        app.processEvents()
        threading.Event().wait(0.001)
    assert predicate()


def media_info(path: Path, *, video: bool = True) -> MediaInfo:
    videos = (
        (
            VideoStreamInfo(
                index=0,
                codec_name="h264",
                width=1920,
                height=1080,
                frame_rate=Fraction(30000, 1001),
                duration=Decimal("12.5"),
            ),
        )
        if video
        else ()
    )
    audios = (
        ()
        if video
        else (
            AudioStreamInfo(
                index=0,
                codec_name="aac",
                sample_rate=48000,
                channels=2,
                duration=Decimal("12.5"),
            ),
        )
    )
    return MediaInfo(
        path=path,
        duration=Decimal("12.5"),
        format_name="matroska",
        size=123,
        video_streams=videos,
        audio_streams=audios,
    )


def runtime_without_tools() -> Runtime:
    return Runtime(
        settings=Settings(),
        logger=logging.getLogger("test"),
        media_tools=None,
    )


def test_extract_local_files_filters_and_deduplicates(tmp_path: Path) -> None:
    media = tmp_path / "clip [ä 中] space.mp4"
    media.touch()
    directory = tmp_path / "folder"
    directory.mkdir()
    urls = [
        QUrl("https://example.com/video.mp4"),
        QUrl.fromLocalFile(str(directory)),
        QUrl.fromLocalFile(str(media)),
        QUrl.fromLocalFile(str(media)),
    ]

    assert extract_local_files(urls) == (media.resolve(),)


def test_missing_tools_keeps_ingest_usable_and_marks_card_error(tmp_path: Path) -> None:
    application()
    media = tmp_path / "clip.mp4"
    media.touch()
    window = MainWindow(runtime_without_tools())

    window.ingest_paths([media])

    browse = window.findChild(QPushButton)
    assert window.drop_target.isEnabled()
    assert browse is not None and browse.isEnabled()
    assert "unavailable" in window.tool_status.text()
    assert len(window._cards) == 1
    assert window._cards[0].state_label.text().startswith("Error")
    assert "ffmpeg and ffprobe" in window._cards[0].detail_label.text()
    nav = window.findChild(QListWidget)
    assert nav is not None and [nav.item(index).text() for index in range(nav.count())] == [
        "Media",
        "Playlist",
    ]
    window.close()


def test_ready_video_and_audio_cards_present_and_select_details(tmp_path: Path) -> None:
    application()
    window = MainWindow(runtime_without_tools())
    video = MediaCard(tmp_path / "video.mkv")
    audio = MediaCard(tmp_path / "audio.m4a")

    video.set_ready(media_info(video.path))
    audio.set_ready(media_info(audio.path, video=False))
    window.show_card(video)

    assert video.state_label.text() == "Ready"
    assert "Video · 1920 × 1080 · h264 · 29.970 fps (30000/1001)" in video.detail_label.text()
    assert "Audio · aac · 48000 Hz · 2 channels" in audio.detail_label.text()
    assert str(video.path) in window.detail_display.text()
    assert "Ready" in window.detail_display.text()
    window.close()


class FakeProbe:
    def __init__(self, result: MediaInfo | Exception, gate: threading.Event | None = None) -> None:
        self.result = result
        self.gate = gate
        self.called = threading.Event()
        self.thread: QThread | None = None

    def probe(self, path: Path | str, timeout: float | None = 30) -> MediaInfo:
        self.thread = QThread.currentThread()
        self.called.set()
        if self.gate is not None:
            self.gate.wait(2)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def test_probe_controller_runs_worker_and_emits_matching_success(tmp_path: Path) -> None:
    app = application()
    fake = FakeProbe(media_info(tmp_path / "video.mkv"))
    controller = ProbeController(fake)
    received: list[tuple[str, MediaInfo]] = []
    controller.succeeded.connect(lambda request_id, info: received.append((request_id, info)))

    request_id = controller.probe(tmp_path / "video.mkv")
    pump_until(lambda: bool(received))

    assert fake.called.is_set()
    assert fake.thread is not app.thread()
    assert received == [(request_id, fake.result)]
    controller.close()


def test_probe_controller_normalizes_failure(tmp_path: Path) -> None:
    application()
    controller = ProbeController(FakeProbe(ValueError("broken media")))
    received: list[tuple[str, str]] = []
    controller.failed.connect(lambda request_id, message: received.append((request_id, message)))

    request_id = controller.probe(tmp_path / "bad.mkv")
    pump_until(lambda: bool(received))

    assert received == [(request_id, "broken media")]
    controller.close()


def test_probe_controller_ignores_late_result_after_close(tmp_path: Path) -> None:
    application()
    gate = threading.Event()
    fake = FakeProbe(media_info(tmp_path / "late.mkv"), gate)
    controller = ProbeController(fake)
    received: list[str] = []
    controller.succeeded.connect(lambda request_id, _: received.append(request_id))

    controller.probe(tmp_path / "late.mkv")
    assert fake.called.wait(2)
    controller.close()
    gate.set()
    pump_until(lambda: controller._pool.activeThreadCount() == 0)

    assert received == []
