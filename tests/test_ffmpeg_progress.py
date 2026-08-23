from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from loopforge.ffmpeg_service import (
    ActiveProcess,
    FFmpegProgress,
    FFmpegProgressParser,
    FFmpegService,
    ProcessCancelledError,
)
from loopforge.media_tools import MediaTools


def service() -> FFmpegService:
    executable = Path(sys.executable)
    return FFmpegService(MediaTools(executable, executable, "python", "python"))


@pytest.mark.parametrize(
    ("lines", "field", "expected"),
    [
        (["out_time_us=2500000", "progress=continue"], "out_time_us", 2500000),
        (["out_time=01:02:03.500000", "progress=continue"], "out_time_us", 3723500000),
        (["speed=1.25x", "progress=continue"], "speed", 1.25),
        (["fps=29.97", "progress=continue"], "fps", 29.97),
        (["frame=42", "progress=continue"], "frame", 42),
        (["total_size=8192", "progress=continue"], "total_size", 8192),
        (["progress=end"], "ended", True),
        (["out_time_us=N/A", "progress=continue"], "out_time_us", None),
        (["fps=broken", "progress=continue"], "fps", None),
        (["out_time=broken", "progress=continue"], "out_time_us", None),
    ],
)
def test_progress_blocks(lines: list[str], field: str, expected: object) -> None:
    parser = FFmpegProgressParser()
    result = None
    for line in lines:
        result = parser.feed(line) or result
    assert result is not None
    assert getattr(result, field) == expected


def test_parser_ignores_malformed_and_resets_blocks() -> None:
    parser = FFmpegProgressParser()
    assert parser.feed("garbage") is None
    assert parser.feed("") is None
    first = parser.feed("progress=continue")
    parser.feed("frame=7")
    second = parser.feed("progress=end")
    assert first is not None and first.frame is None
    assert second is not None and second.frame == 7 and second.ended


def test_streaming_drains_large_streams_and_calls_callbacks() -> None:
    progress: list[FFmpegProgress] = []
    logs: list[str] = []
    code = (
        "import sys\n"
        "for i in range(6000):\n"
        " print(f'frame={i}')\n"
        " print('x'*200, file=sys.stderr)\n"
        "print('out_time_us=1000000')\n"
        "print('speed=2x')\n"
        "print('progress=end')\n"
    )
    result = service().run(
        ["-c", code], streaming=True, timeout=15, on_progress=progress.append, on_log=logs.append
    )
    assert result.returncode == 0
    assert len(logs) == 6000
    assert progress[-1].ended and progress[-1].speed == 2
    assert len(result.stdout) > 60000 and len(result.stderr) > 1_000_000


def test_streaming_callbacks_may_fail_without_stopping_drain() -> None:
    result = service().run(
        ["-c", "print('progress=end'); import sys; print('log', file=sys.stderr)"],
        streaming=True,
        on_progress=lambda _value: (_ for _ in ()).throw(RuntimeError("callback")),
        on_log=lambda _value: (_ for _ in ()).throw(RuntimeError("callback")),
    )
    assert result.returncode == 0


def test_active_process_cancellation_is_idempotent() -> None:
    process = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
        start_new_session=sys.platform != "win32",
    )
    active = ActiveProcess(process, (sys.executable, "-c", "sleep"))
    try:
        active.cancel(0.2)
        active.cancel(0.2)
        with pytest.raises(ProcessCancelledError):
            active.wait()
        assert active.cancelled and not active.running
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
