from __future__ import annotations

import logging
import re
import threading
from typing import Literal

from .ffmpeg_service import FFmpegError, FFmpegService
from .models import (
    AudioEncoderCapability,
    CodecFamily,
    EncoderBackend,
    EncoderCapability,
    HardwareCapabilities,
)

logger = logging.getLogger(__name__)

_ENCODER_LINE = re.compile(r"^\s*(\S{6})\s+(\S+)\s*(.*)$")
_VIDEO_ENCODERS: dict[str, tuple[CodecFamily, EncoderBackend]] = {
    "libx264": ("h264", "cpu"),
    "libx265": ("hevc", "cpu"),
    "libsvtav1": ("av1", "cpu"),
    "libaom-av1": ("av1", "cpu"),
    "librav1e": ("av1", "cpu"),
    "h264_nvenc": ("h264", "nvenc"),
    "h264_qsv": ("h264", "qsv"),
    "h264_amf": ("h264", "amf"),
    "hevc_nvenc": ("hevc", "nvenc"),
    "hevc_qsv": ("hevc", "qsv"),
    "hevc_amf": ("hevc", "amf"),
    "av1_nvenc": ("av1", "nvenc"),
    "av1_qsv": ("av1", "qsv"),
    "av1_amf": ("av1", "amf"),
}
_AUDIO_ENCODERS: dict[str, Literal["aac", "opus"]] = {"aac": "aac", "libopus": "opus"}


class HardwareDetectionError(RuntimeError):
    pass


def parse_video_encoders(output: str) -> HardwareCapabilities:
    encoders: list[EncoderCapability] = []
    audio_encoders: list[AudioEncoderCapability] = []
    seen: set[str] = set()
    for line in output.splitlines():
        match = _ENCODER_LINE.match(line)
        if not match:
            continue
        name = match.group(2).lower()
        if name in seen:
            continue
        description = match.group(3).strip()
        if match.group(1)[0] == "V" and name in _VIDEO_ENCODERS:
            codec, backend = _VIDEO_ENCODERS[name]
            encoders.append(EncoderCapability(name, codec, backend, description))
            seen.add(name)
        elif match.group(1)[0] == "A" and name in _AUDIO_ENCODERS:
            audio_encoders.append(AudioEncoderCapability(name, _AUDIO_ENCODERS[name], description))
            seen.add(name)
    return HardwareCapabilities(tuple(encoders), tuple(audio_encoders))


class HardwareDetector:
    def __init__(self, ffmpeg: FFmpegService) -> None:
        self.ffmpeg = ffmpeg
        self._cached: HardwareCapabilities | None = None
        self._lock = threading.Lock()

    def detect(self, *, refresh: bool = False, timeout: float | None = 10) -> HardwareCapabilities:
        with self._lock:
            if self._cached is not None and not refresh:
                logger.debug("Using cached hardware capabilities")
                return self._cached
            logger.info("Detecting FFmpeg encoder capabilities")
            try:
                result = self.ffmpeg.run(["-hide_banner", "-encoders"], timeout=timeout)
            except FFmpegError as error:
                detail = error.result.stderr.strip() if error.result else str(error)
                raise HardwareDetectionError(f"Unable to detect encoders: {detail}") from error
            self._cached = parse_video_encoders(f"{result.stdout}\n{result.stderr}")
            logger.info("Detected %d supported encoders", len(self._cached.encoders))
            return self._cached
