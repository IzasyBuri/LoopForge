from decimal import Decimal
from fractions import Fraction
from pathlib import Path

import pytest

from loopforge.ffmpeg_service import FFmpegError
from loopforge.media_probe import (
    MediaPathError,
    MediaProbeError,
    MediaProbeService,
    parse_decimal,
    parse_fraction,
)
from loopforge.models import ProcessResult


class FakeFFmpeg:
    def __init__(self, result: ProcessResult | None = None) -> None:
        self.result = result
        self.args: list[str] = []

    def run(
        self,
        args: list[str],
        *,
        probe: bool = False,
        timeout: float | None = None,
        check: bool = True,
    ) -> ProcessResult:
        assert probe and timeout == 30 and check
        assert args[-1]
        self.args = args
        if self.result is None:
            raise FFmpegError("failed", ProcessResult(tuple(args), 1, "", "invalid media"))
        return self.result


def test_exact_parsers() -> None:
    assert parse_fraction("30000/1001") == Fraction(30000, 1001)
    assert parse_fraction("0/0") is None
    assert parse_fraction("bad") is None
    assert parse_decimal("0.100000000000000001") == Decimal("0.100000000000000001")
    assert parse_decimal("NaN") is None
    assert parse_decimal(-1) is None


def test_probe_parses_stream_types_and_exact_values(tmp_path: Path) -> None:
    media = tmp_path / "clip [ä 中].mkv"
    media.touch()
    payload = """{"streams":[
      {"index":0,"codec_type":"video","codec_name":"h264","width":1920,"height":1080,
       "avg_frame_rate":"30000/1001","duration":"1.001"},
      {"index":1,"codec_type":"audio","codec_name":"aac","sample_rate":"48000",
       "channels":2,"duration":"1.001"},
      {"index":2,"codec_type":"subtitle","codec_name":"ass"}],
      "format":{"format_name":"matroska","duration":"1.001","size":"123"}}"""
    fake = FakeFFmpeg(ProcessResult(("ffprobe",), 0, payload, ""))

    info = MediaProbeService(fake).probe(media)  # type: ignore[arg-type]

    assert info.path == media.resolve()
    assert info.duration == Decimal("1.001")
    assert info.video_streams[0].frame_rate == Fraction(30000, 1001)
    assert info.audio_streams[0].sample_rate == 48000
    assert info.size == 123


def test_count_frames_argument_and_nb_read_frames(tmp_path: Path) -> None:
    media = tmp_path / "counted.mkv"
    media.touch()
    payload = '{"streams":[{"index":0,"codec_type":"video","nb_read_frames":"17"}]}'
    fake = FakeFFmpeg(ProcessResult(("ffprobe",), 0, payload, ""))
    info = MediaProbeService(fake).probe(media, count_frames=True)  # type: ignore[arg-type]
    assert fake.args == [
        "-v",
        "error",
        "-count_frames",
        "-show_format",
        "-show_streams",
        "-of",
        "json",
        str(media.resolve()),
    ]
    assert info.video_streams[0].counted_frame_count == 17

    fake.result = ProcessResult(
        ("ffprobe",), 0, '{"streams":[{"index":0,"codec_type":"video","nb_read_frames":"N/A"}]}', ""
    )
    service = MediaProbeService(fake)  # type: ignore[arg-type]
    assert service.probe(media, count_frames=True).video_streams[0].counted_frame_count is None


def test_probe_rejects_nonexistent_and_directory(tmp_path: Path) -> None:
    service = MediaProbeService(FakeFFmpeg())  # type: ignore[arg-type]
    with pytest.raises(MediaPathError):
        service.probe(tmp_path / "missing")
    with pytest.raises(MediaPathError):
        service.probe(tmp_path)


def test_probe_wraps_process_and_json_errors(tmp_path: Path) -> None:
    media = tmp_path / "bad"
    media.touch()
    with pytest.raises(MediaProbeError, match="invalid media"):
        MediaProbeService(FakeFFmpeg()).probe(media)  # type: ignore[arg-type]
    invalid = FakeFFmpeg(ProcessResult(("ffprobe",), 0, "not json", ""))
    with pytest.raises(MediaProbeError, match="invalid JSON"):
        MediaProbeService(invalid).probe(media)  # type: ignore[arg-type]


def test_fraction_edge_cases_are_exact() -> None:
    for value, expected in (
        ("24000/1001", Fraction(24000, 1001)),
        ("30000/1001", Fraction(30000, 1001)),
        ("60000/1001", Fraction(60000, 1001)),
        ("1/90000", Fraction(1, 90000)),
        ("0/0", None),
        ("N/A", None),
        ("", None),
        (None, None),
    ):
        assert parse_fraction(value) == expected


def test_probe_preserves_full_metadata_and_nonzero_indexes(tmp_path: Path) -> None:
    media = tmp_path / "full.mkv"
    media.touch()
    payload = """{"streams":[
      {"index":3,"codec_type":"video","codec_name":"h264","codec_long_name":"H.264",
       "pix_fmt":"yuv420p","width":1920,"height":1080,"avg_frame_rate":"24000/1001",
       "r_frame_rate":"30000/1001","time_base":"1/90000","duration":"10.01",
       "duration_ts":"900900","start_time":"0.001","nb_frames":"240","bit_rate":"5000000"},
      {"index":7,"codec_type":"audio","codec_name":"aac","codec_long_name":"AAC",
       "sample_rate":"48000","channels":2,"channel_layout":"stereo","time_base":"1/48000",
       "duration":"10.01","duration_ts":"480480","start_time":"0","bit_rate":"192000"}],
      "format":{"format_name":"matroska","format_long_name":"Matroska",
      "start_time":"0.001","duration":"10.01","bit_rate":"5192000","size":"6490000"}}"""
    info = MediaProbeService(FakeFFmpeg(ProcessResult(("ffprobe",), 0, payload, ""))).probe(media)  # type: ignore[arg-type]

    video = info.primary_video_stream
    audio = info.primary_audio_stream
    assert info.has_video and info.has_audio and video is not None and audio is not None
    assert (info.format_long_name, info.start_time, info.bitrate, info.file_size) == (
        "Matroska",
        Decimal("0.001"),
        5192000,
        6490000,
    )
    assert (video.index, video.codec_long_name, video.pixel_format) == (3, "H.264", "yuv420p")
    assert (video.avg_frame_rate, video.real_frame_rate, video.time_base) == (
        Fraction(24000, 1001),
        Fraction(30000, 1001),
        Fraction(1, 90000),
    )
    assert video.authoritative_duration == Decimal("10.01")
    assert (video.frame_count, video.bitrate, video.width, video.height) == (
        240,
        5000000,
        1920,
        1080,
    )
    assert (audio.index, audio.codec_long_name, audio.channel_layout) == (7, "AAC", "stereo")
    assert (audio.time_base, audio.duration_ts, audio.start_time, audio.bitrate) == (
        Fraction(1, 48000),
        480480,
        Decimal("0"),
        192000,
    )


def test_probe_tolerates_malformed_optional_fields_and_requires_media_stream(
    tmp_path: Path,
) -> None:
    media = tmp_path / "odd.mkv"
    media.touch()
    malformed = """{"streams":[{"index":4,"codec_type":"video","width":"bad",
      "avg_frame_rate":"N/A","r_frame_rate":"0/0","duration_ts":"bad"}],"format":null}"""
    info = MediaProbeService(FakeFFmpeg(ProcessResult(("ffprobe",), 0, malformed, ""))).probe(media)  # type: ignore[arg-type]
    assert info.primary_video_stream is not None
    assert info.primary_video_stream.width is None
    assert info.primary_video_stream.frame_rate is None

    empty = FakeFFmpeg(
        ProcessResult(("ffprobe",), 0, '{"streams":[{"index":2,"codec_type":"subtitle"}]}', "")
    )
    with pytest.raises(MediaProbeError, match="no recognized"):
        MediaProbeService(empty).probe(media)  # type: ignore[arg-type]
