import wave
from fractions import Fraction
from pathlib import Path

import pytest

from loopforge.config import Settings
from loopforge.encoding import EncodingSettings
from loopforge.encoding_engine import EncodingEngine, EncodingError
from loopforge.ffmpeg_service import FFmpegService
from loopforge.hardware import HardwareDetector
from loopforge.media_probe import MediaProbeError, MediaProbeService
from loopforge.media_tools import DiscoveryError, discover_media_tools
from loopforge.models import HardwareCapabilities
from loopforge.output_validation import OutputValidationService
from loopforge.timeline import VideoLoopEngine


def services() -> tuple[FFmpegService, MediaProbeService]:
    try:
        tools = discover_media_tools(Settings())
    except DiscoveryError:
        pytest.skip("ffmpeg and ffprobe unavailable")
    ffmpeg = FFmpegService(tools)
    return ffmpeg, MediaProbeService(ffmpeg)


def make_inputs(path: Path) -> tuple[Path, Path]:
    video = path / "frame [ä 中].ppm"
    video.write_bytes(b"P6\n16 16\n255\n" + bytes(16 * 16 * 3))
    audio = path / "sound [ä 中].wav"
    with wave.open(str(audio), "wb") as output:
        output.setparams((1, 2, 8000, 800, "NONE", "not compressed"))
        output.writeframes(bytes(1600))
    return video, audio


@pytest.mark.parametrize(
    ("name", "kind", "video_count", "audio_count"),
    [
        ("video only [ä 中].mkv", "video", 1, 0),
        ("audio only [ä 中].mka", "audio", 0, 1),
        ("audio video [ä 中].mkv", "av", 1, 1),
    ],
)
def test_real_probe_media_types_and_special_paths(
    tmp_path: Path,
    name: str,
    kind: str,
    video_count: int,
    audio_count: int,
) -> None:
    ffmpeg, probe = services()
    video, audio = make_inputs(tmp_path)
    output = tmp_path / name
    args = ["-hide_banner", "-loglevel", "error"]
    if kind in {"video", "av"}:
        args.extend(["-framerate", "30000/1001", "-loop", "1", "-t", "0.1", "-i", str(video)])
    if kind in {"audio", "av"}:
        args.extend(["-i", str(audio)])
    args.extend(["-c:v", "ffv1", "-c:a", "pcm_s16le", "-shortest", "-y", str(output)])
    ffmpeg.run(args)

    info = probe.probe(output)

    assert len(info.video_streams) == video_count
    assert len(info.audio_streams) == audio_count
    if info.video_streams:
        assert info.video_streams[0].frame_rate == Fraction(30000, 1001)
        counted = probe.probe(output, count_frames=True).video_streams[0]
        assert counted.counted_frame_count is not None
        assert counted.counted_frame_count > 0


def test_real_invalid_media_and_encoder_detection(tmp_path: Path) -> None:
    ffmpeg, probe = services()
    invalid = tmp_path / "invalid [ä 中].bin"
    invalid.write_text("not media", encoding="utf-8")
    with pytest.raises(MediaProbeError):
        probe.probe(invalid)

    capabilities = HardwareDetector(ffmpeg).detect()
    assert all(item.codec in {"h264", "hevc", "av1"} for item in capabilities.encoders)


def test_real_encoding_engine_loops_partial_tail_and_publishes(tmp_path: Path) -> None:
    ffmpeg, probe = services()
    capabilities = HardwareDetector(ffmpeg).detect()
    if not capabilities.has_encoder("libx264"):
        pytest.skip("libx264 unavailable")
    source = tmp_path / "source [ä 中] 3.mkv"
    ffmpeg.run(
        (
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=16x16:rate=7",
            "-frames:v",
            "3",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-y",
            str(source),
        )
    )
    counted = probe.probe(source, count_frames=True).primary_video_stream
    assert counted is not None and counted.counted_frame_count == 3
    plan = VideoLoopEngine().for_target_duration(
        fps=Fraction(7),
        source_duration=Fraction(3, 7),
        target_duration=Fraction(8, 7),
        counted_frame_count=counted.counted_frame_count,
    )
    output = tmp_path / "output [ä 中] partial.mp4"
    settings = EncodingSettings("Custom", "h264", "none", "mp4", "cpu", 20, "medium")
    result = EncodingEngine(ffmpeg, OutputValidationService(probe)).render(
        source, output, plan, settings, capabilities
    )
    validation = OutputValidationService(probe).validate(output, plan)
    assert result.path == output and output.is_file()
    assert validation.valid and validation.actual_frame_count == 8


def test_real_encoding_failure_preserves_existing_output_and_removes_temp(tmp_path: Path) -> None:
    ffmpeg, probe = services()
    source = tmp_path / "invalid source [ä 中].mkv"
    source.write_bytes(b"not media")
    output = tmp_path / "existing output [ä 中].mp4"
    output.write_bytes(b"preserve")
    plan = VideoLoopEngine().for_repeat_count(
        fps=Fraction(7), source_duration=Fraction(1, 7), repeat_count=1
    )
    settings = EncodingSettings("Custom", "h264", "none", "mp4", "cpu", 20, "medium")
    capabilities = HardwareCapabilities(
        tuple(item for item in HardwareDetector(ffmpeg).detect().encoders if item.name == "libx264")
    )
    if not capabilities.has_encoder("libx264"):
        pytest.skip("libx264 unavailable")
    with pytest.raises(EncodingError):
        EncodingEngine(ffmpeg, OutputValidationService(probe)).render(
            source, output, plan, settings, capabilities, overwrite=True
        )
    assert output.read_bytes() == b"preserve"
    assert not (tmp_path / ".existing output [ä 中].loopforge-tmp.mp4").exists()
