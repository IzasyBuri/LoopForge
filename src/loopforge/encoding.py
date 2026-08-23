from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .models import HardwareCapabilities

VideoCodec = Literal["h264", "hevc", "av1"]
AudioCodec = Literal["aac", "opus", "none"]
Container = Literal["mp4", "mkv"]
Backend = Literal["auto", "cpu", "nvenc", "qsv", "amf"]

CPU_ENCODERS: dict[VideoCodec, tuple[str, ...]] = {
    "h264": ("libx264",),
    "hevc": ("libx265",),
    "av1": ("libsvtav1", "libaom-av1", "librav1e"),
}
HARDWARE_ENCODERS: dict[tuple[VideoCodec, Backend], str] = {
    ("h264", "nvenc"): "h264_nvenc",
    ("h264", "qsv"): "h264_qsv",
    ("h264", "amf"): "h264_amf",
    ("hevc", "nvenc"): "hevc_nvenc",
    ("hevc", "qsv"): "hevc_qsv",
    ("hevc", "amf"): "hevc_amf",
    ("av1", "nvenc"): "av1_nvenc",
    ("av1", "qsv"): "av1_qsv",
    ("av1", "amf"): "av1_amf",
}


@dataclass(frozen=True, slots=True)
class EncodingSettings:
    name: str
    video_codec: VideoCodec
    audio_codec: AudioCodec
    container: Container
    backend: Backend
    quality: int
    speed: str

    def __post_init__(self) -> None:
        if self.container == "mp4" and self.audio_codec == "opus":
            raise ValueError("Opus audio requires MKV")
        if not 0 <= self.quality <= 63:
            raise ValueError("quality must be between 0 and 63")
        if not self.speed:
            raise ValueError("speed is required")

    @property
    def extension(self) -> str:
        return f".{self.container}"

    def validate_output(self, suffix: str) -> None:
        if suffix.lower() != self.extension:
            raise ValueError(f"output extension must be {self.extension}")


ULTRA_FAST = EncodingSettings("Ultra Fast", "h264", "aac", "mp4", "auto", 28, "ultrafast")
FAST = EncodingSettings("Fast", "h264", "aac", "mp4", "auto", 24, "fast")
BALANCED = EncodingSettings("Balanced", "h264", "aac", "mp4", "auto", 21, "medium")
QUALITY = EncodingSettings("Quality", "hevc", "aac", "mp4", "auto", 20, "slow")
MAXIMUM_QUALITY = EncodingSettings("Maximum Quality", "av1", "none", "mkv", "cpu", 18, "slow")
CUSTOM = EncodingSettings("Custom", "h264", "none", "mp4", "auto", 21, "medium")
PRESETS = (ULTRA_FAST, FAST, BALANCED, QUALITY, MAXIMUM_QUALITY, CUSTOM)


@dataclass(frozen=True, slots=True)
class EncoderSelection:
    name: str
    backend: Literal["cpu", "nvenc", "qsv", "amf"]


class EncodingCompatibilityError(ValueError):
    pass


def select_encoder(
    settings: EncodingSettings, capabilities: HardwareCapabilities
) -> EncoderSelection:
    if settings.backend == "auto":
        for backend in ("nvenc", "qsv", "amf"):
            name = HARDWARE_ENCODERS.get((settings.video_codec, backend))
            if name is not None and capabilities.has_encoder(name):
                return EncoderSelection(name, backend)
        return _select_cpu(settings.video_codec, capabilities)
    if settings.backend == "cpu":
        return _select_cpu(settings.video_codec, capabilities)
    name = HARDWARE_ENCODERS.get((settings.video_codec, settings.backend))
    if name is None or not capabilities.has_encoder(name):
        raise EncodingCompatibilityError(
            f"{settings.backend} does not advertise {settings.video_codec} support"
        )
    return EncoderSelection(name, settings.backend)


def _select_cpu(codec: VideoCodec, capabilities: HardwareCapabilities) -> EncoderSelection:
    for name in CPU_ENCODERS[codec]:
        if capabilities.has_encoder(name):
            return EncoderSelection(name, "cpu")
    raise EncodingCompatibilityError(f"no supported CPU {codec} encoder is advertised")
