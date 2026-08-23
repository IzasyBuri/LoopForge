from __future__ import annotations

import threading
from fractions import Fraction
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer

from loopforge.config import Settings
from loopforge.encoding import EncodingSettings
from loopforge.encoding_engine import EncodingEngine
from loopforge.ffmpeg_service import FFmpegService
from loopforge.hardware import HardwareDetector
from loopforge.media_probe import MediaProbeService
from loopforge.media_tools import DiscoveryError, discover_media_tools
from loopforge.output_validation import OutputValidationService
from loopforge.playlist import Playlist, PlaylistEngine, track_from_media
from loopforge.render_tasks import RenderController
from loopforge.renderer import RenderRequest, VideoMusicRenderer
from loopforge.timeline import VideoLoopEngine


def services() -> tuple[FFmpegService, MediaProbeService]:
    try:
        tools = discover_media_tools(Settings())
    except DiscoveryError:
        pytest.skip("ffmpeg and ffprobe unavailable")
    ffmpeg = FFmpegService(tools)
    return ffmpeg, MediaProbeService(ffmpeg)


def test_real_renderer_exact_video_and_audio(tmp_path: Path) -> None:
    ffmpeg, probe = services()
    capabilities = HardwareDetector(ffmpeg).detect()
    if not capabilities.has_encoder("libx264") or not capabilities.supports_audio("aac"):
        pytest.skip("libx264 or aac unavailable")
    source = tmp_path / "源 [three frames] video.mkv"
    first = tmp_path / "音 [A mono] track.wav"
    second = tmp_path / "音 [B stereo] track.wav"
    ffmpeg.run(
        (
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=32x32:rate=30",
            "-frames:v",
            "3",
            "-c:v",
            "ffv1",
            "-y",
            str(source),
        )
    )
    ffmpeg.run(
        (
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=8000:duration=0.2",
            "-ac",
            "1",
            "-c:a",
            "pcm_s16le",
            "-y",
            str(first),
        )
    )
    ffmpeg.run(
        (
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=880:sample_rate=16000:duration=0.2",
            "-ac",
            "2",
            "-c:a",
            "pcm_s16le",
            "-y",
            str(second),
        )
    )
    tracks = (
        track_from_media(probe.probe(first), lambda: "a"),
        track_from_media(probe.probe(second), lambda: "b"),
    )
    target = Fraction(8, 30)
    video_plan = VideoLoopEngine().for_target_duration(
        fps=Fraction(30),
        source_duration=Fraction(1, 10),
        target_duration=target,
        counted_frame_count=3,
    )
    audio_plan = PlaylistEngine().render_target(Playlist(tracks), target)
    settings = EncodingSettings("E2E", "h264", "aac", "mp4", "cpu", 20, "fast")
    output = tmp_path / "完成 [render] output.mp4"
    renderer = VideoMusicRenderer(ffmpeg, EncodingEngine(ffmpeg, OutputValidationService(probe)))
    result = renderer.render(
        RenderRequest(source, output, video_plan, audio_plan, settings, capabilities)
    )
    info = probe.probe(output, count_frames=True)
    video = info.primary_video_stream
    audio = info.primary_audio_stream
    assert result.path == output
    assert video is not None and video.counted_frame_count == 8 and video.codec_name == "h264"
    assert audio is not None and audio.codec_name == "aac" and audio.sample_rate and audio.channels
    assert audio.duration is not None or (
        audio.duration_ts is not None and audio.time_base is not None
    )
    duration = (
        Fraction(audio.duration_ts) * audio.time_base
        if audio.duration_ts is not None and audio.time_base is not None
        else Fraction(str(audio.duration))
    )
    assert abs(duration - target) <= max(
        Fraction(1024, audio.sample_rate), audio.time_base or Fraction()
    )
    assert not list(tmp_path.glob(".loopforge-*"))
    print(
        f"RENDER_METADATA frames={video.counted_frame_count} video={video.codec_name} "
        f"audio={audio.codec_name} sample_rate={audio.sample_rate} channels={audio.channels} "
        f"audio_duration={duration}"
    )


class WorkerRenderer:
    def __init__(self, error: bool = False) -> None:
        self.error = error
        self.thread = threading.current_thread()

    def render(self, request: Any, *, timeout: float | None = None) -> object:
        self.thread = threading.current_thread()
        if self.error:
            raise RuntimeError("worker failed")
        return request


def wait_signal(controller: RenderController, success: bool) -> tuple[str, object]:
    app = QCoreApplication.instance() or QCoreApplication([])
    loop = QEventLoop()
    received: list[tuple[str, object]] = []
    signal = controller.succeeded if success else controller.failed

    def receive(identity: str, value: object) -> None:
        received.append((identity, value))
        loop.quit()

    signal.connect(receive)
    QTimer.singleShot(5000, loop.quit)
    loop.exec()
    app.processEvents()
    assert received
    return received[0]


def test_render_controller_nonblocking_success_failure_and_close() -> None:
    main = threading.current_thread()
    service = WorkerRenderer()
    controller = RenderController(service)  # type: ignore[arg-type]
    identity = controller.render("request")  # type: ignore[arg-type]
    received = wait_signal(controller, True)
    assert received == (identity, "request") and service.thread is not main
    failed_service = WorkerRenderer(True)
    failed = RenderController(failed_service)  # type: ignore[arg-type]
    failed_id = failed.render("request")  # type: ignore[arg-type]
    assert wait_signal(failed, False) == (failed_id, "worker failed")
    failed.close()
    with pytest.raises(RuntimeError, match="closed"):
        failed.render("request")  # type: ignore[arg-type]
    controller.close()
