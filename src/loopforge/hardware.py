from __future__ import annotations

import logging
import re
import threading

from .ffmpeg_service import FFmpegError, FFmpegService
from .models import CodecFamily, EncoderBackend, EncoderCapability, HardwareCapabilities

logger = logging.getLogger(__name__)

_ENCODER_LINE = re.compile(r"^\s*(\S{6})\s+(\S+)\s*(.*)$")


class HardwareDetectionError(RuntimeError):
    pass


def parse_video_encoders(output: str) -> HardwareCapabilities:
    encoders: list[EncoderCapability] = []
    for line in output.splitlines():
        match = _ENCODER_LINE.match(line)
        if not match or match.group(1)[0] != "V":
            continue
        name = match.group(2).lower()
        codec: CodecFamily
        if "264" in name or name.startswith("h264"):
            codec = "h264"
        elif "265" in name or "hevc" in name:
            codec = "hevc"
        elif "av1" in name:
            codec = "av1"
        else:
            continue
        backend: EncoderBackend
        if "nvenc" in name:
            backend = "nvenc"
        elif "qsv" in name:
            backend = "qsv"
        elif "amf" in name:
            backend = "amf"
        else:
            backend = "cpu"
        encoders.append(EncoderCapability(name, codec, backend, match.group(3).strip()))
    return HardwareCapabilities(tuple(encoders))


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
