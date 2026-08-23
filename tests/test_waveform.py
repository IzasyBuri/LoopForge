import threading
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import cast

import pytest
from PySide6.QtCore import QThread
from PySide6.QtWidgets import QApplication

from loopforge.models import ProcessResult
from loopforge.waveform import WaveformController, WaveformRequest, WaveformService


class FakeFFmpeg:
    def __init__(self, fail: bool = False) -> None:
        self.args: list[str] = []
        self.fail = fail

    def run(self, args: Sequence[str], **_: object) -> ProcessResult:
        self.args = list(args)
        output = Path(self.args[-1])
        output.write_bytes(b"png")
        if self.fail:
            raise RuntimeError("failed")
        return ProcessResult(tuple(self.args), 0, "", "")


class FakeRenderer:
    def __init__(self, output: Path, gate: threading.Event | None = None) -> None:
        self.output = output
        self.gate = gate
        self.thread: QThread | None = None

    def render(self, request: WaveformRequest) -> Path:
        self.thread = QThread.currentThread()
        if self.gate:
            self.gate.wait(2)
        return self.output


def app() -> QApplication:
    current = QApplication.instance()
    return QApplication([]) if current is None else cast(QApplication, current)


def pump(predicate: Callable[[], bool]) -> None:
    deadline = time.monotonic() + 3
    while not predicate() and time.monotonic() < deadline:
        app().processEvents()
        time.sleep(0.001)
    assert predicate()


def test_cache_key_hit_safe_args_and_atomic_cleanup(tmp_path: Path) -> None:
    source = tmp_path / "a [x].wav"
    source.touch()
    request = WaveformRequest(source, 2, 80, 20)
    assert request.cache_key == request.cache_key
    fake = FakeFFmpeg()
    service = WaveformService(cast(object, fake), tmp_path / "cache")  # type: ignore[arg-type]
    output = service.render(request)
    assert output.is_file()
    assert fake.args == [
        "-nostdin",
        "-y",
        "-i",
        str(source.resolve()),
        "-map",
        "0:2",
        "-filter_complex",
        "showwavespic=s=80x20",
        "-frames:v",
        "1",
        str(output.with_suffix(".tmp.png")),
    ]
    fake.args = []
    assert service.render(request) == output
    assert fake.args == []
    failing = FakeFFmpeg(True)
    source.write_bytes(b"changed")
    with pytest.raises(RuntimeError):
        WaveformService(cast(object, failing), tmp_path / "cache").render(request)  # type: ignore[arg-type]
    assert not Path(failing.args[-1]).exists()


def test_controller_worker_success_and_close_ignores_late(tmp_path: Path) -> None:
    application = app()
    renderer = FakeRenderer(tmp_path / "wave.png")
    controller = WaveformController(renderer)
    received: list[str] = []
    controller.succeeded.connect(lambda request_id, _: received.append(request_id))
    request_id = controller.render(WaveformRequest(tmp_path / "a.wav", 0, 10, 10))
    pump(lambda: bool(received))
    assert received == [request_id]
    assert renderer.thread is not application.thread()
    gate = threading.Event()
    late = FakeRenderer(tmp_path / "late.png", gate)
    controller = WaveformController(late)
    received = []
    controller.succeeded.connect(lambda request_id, _: received.append(request_id))
    controller.render(WaveformRequest(tmp_path / "b.wav", 0, 10, 10))
    pump(lambda: late.thread is not None)
    controller.close()
    gate.set()
    pump(lambda: controller._pool.activeThreadCount() == 0)
    assert received == []
