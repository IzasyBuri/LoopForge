from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from pathlib import Path

from .ffmpeg_service import FFmpegError, FFmpegService
from .models import AudioStreamInfo, MediaInfo, VideoStreamInfo

logger = logging.getLogger(__name__)


class MediaProbeError(RuntimeError):
    pass


class MediaPathError(MediaProbeError):
    pass


def parse_fraction(value: object) -> Fraction | None:
    if not isinstance(value, str) or not value or value in {"0/0", "N/A"}:
        return None
    try:
        result = Fraction(value)
    except (ValueError, ZeroDivisionError):
        return None
    return result if result > 0 else None


def parse_decimal(value: object) -> Decimal | None:
    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
    except InvalidOperation:
        return None
    return result if result.is_finite() and result >= 0 else None


def parse_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value) if isinstance(value, (str, int)) else None
    except ValueError:
        return None


def _string(value: object) -> str | None:
    return value if isinstance(value, str) else None


class MediaProbeService:
    def __init__(self, ffmpeg: FFmpegService) -> None:
        self.ffmpeg = ffmpeg

    def probe(self, path: Path | str, timeout: float | None = 30) -> MediaInfo:
        media_path = Path(path).expanduser()
        if not media_path.exists():
            raise MediaPathError(f"Media path does not exist: {media_path}")
        if not media_path.is_file():
            raise MediaPathError(f"Media path is not a file: {media_path}")
        resolved = media_path.resolve()
        logger.info("Probing media %s", resolved)
        try:
            result = self.ffmpeg.run(
                ["-v", "error", "-show_format", "-show_streams", "-of", "json", str(resolved)],
                probe=True,
                timeout=timeout,
            )
        except FFmpegError as error:
            detail = error.result.stderr.strip() if error.result else str(error)
            logger.error("Media probe failed for %s: %s", resolved, detail)
            raise MediaProbeError(f"ffprobe failed for {resolved}: {detail}") from error
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as error:
            raise MediaProbeError(f"ffprobe returned invalid JSON for {resolved}") from error
        if not isinstance(payload, Mapping):
            raise MediaProbeError(f"ffprobe returned invalid data for {resolved}")
        info = self._parse(resolved, payload)
        logger.info(
            "Probed media %s: %d video, %d audio streams",
            resolved,
            len(info.video_streams),
            len(info.audio_streams),
        )
        return info

    @staticmethod
    def _parse(path: Path, payload: Mapping[object, object]) -> MediaInfo:
        streams = payload.get("streams", [])
        if not isinstance(streams, list):
            raise MediaProbeError("ffprobe streams value is not a list")
        videos: list[VideoStreamInfo] = []
        audios: list[AudioStreamInfo] = []
        for value in streams:
            if not isinstance(value, Mapping):
                continue
            raw: Mapping[object, object] = value
            index = parse_int(raw.get("index"))
            if index is None:
                continue
            codec = _string(raw.get("codec_name"))
            if raw.get("codec_type") == "video":
                average = parse_fraction(raw.get("avg_frame_rate"))
                real = parse_fraction(raw.get("r_frame_rate"))
                videos.append(
                    VideoStreamInfo(
                        index=index,
                        codec_name=codec,
                        width=parse_int(raw.get("width")),
                        height=parse_int(raw.get("height")),
                        frame_rate=average or real,
                        duration=parse_decimal(raw.get("duration")),
                        codec_long_name=_string(raw.get("codec_long_name")),
                        pixel_format=_string(raw.get("pix_fmt")),
                        avg_frame_rate=average,
                        real_frame_rate=real,
                        time_base=parse_fraction(raw.get("time_base")),
                        duration_ts=parse_int(raw.get("duration_ts")),
                        start_time=parse_decimal(raw.get("start_time")),
                        frame_count=parse_int(raw.get("nb_frames")),
                        bitrate=parse_int(raw.get("bit_rate")),
                    )
                )
            elif raw.get("codec_type") == "audio":
                audios.append(
                    AudioStreamInfo(
                        index=index,
                        codec_name=codec,
                        sample_rate=parse_int(raw.get("sample_rate")),
                        channels=parse_int(raw.get("channels")),
                        duration=parse_decimal(raw.get("duration")),
                        codec_long_name=_string(raw.get("codec_long_name")),
                        channel_layout=_string(raw.get("channel_layout")),
                        time_base=parse_fraction(raw.get("time_base")),
                        duration_ts=parse_int(raw.get("duration_ts")),
                        start_time=parse_decimal(raw.get("start_time")),
                        bitrate=parse_int(raw.get("bit_rate")),
                    )
                )
        if not videos and not audios:
            raise MediaProbeError("ffprobe returned no recognized audio or video streams")
        raw_format = payload.get("format", {})
        media_format: Mapping[object, object] = (
            raw_format if isinstance(raw_format, Mapping) else {}
        )
        return MediaInfo(
            path=path,
            duration=parse_decimal(media_format.get("duration")),
            format_name=_string(media_format.get("format_name")),
            size=parse_int(media_format.get("size")),
            video_streams=tuple(videos),
            audio_streams=tuple(audios),
            format_long_name=_string(media_format.get("format_long_name")),
            start_time=parse_decimal(media_format.get("start_time")),
            bitrate=parse_int(media_format.get("bit_rate")),
        )
