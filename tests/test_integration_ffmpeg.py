import wave
from fractions import Fraction
from pathlib import Path

import pytest

from loopforge.config import Settings
from loopforge.ffmpeg_service import FFmpegService
from loopforge.hardware import HardwareDetector
from loopforge.media_probe import MediaProbeError, MediaProbeService
from loopforge.media_tools import DiscoveryError, discover_media_tools


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
