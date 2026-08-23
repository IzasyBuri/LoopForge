from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from typing import Literal


@dataclass(frozen=True, slots=True)
class ProcessResult:
    args: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str


@dataclass(frozen=True, slots=True)
class VideoStreamInfo:
    index: int
    codec_name: str | None
    width: int | None
    height: int | None
    frame_rate: Fraction | None
    duration: Decimal | None
    codec_long_name: str | None = None
    pixel_format: str | None = None
    avg_frame_rate: Fraction | None = None
    real_frame_rate: Fraction | None = None
    time_base: Fraction | None = None
    duration_ts: int | None = None
    start_time: Decimal | None = None
    frame_count: int | None = None
    bitrate: int | None = None

    @property
    def authoritative_duration(self) -> Decimal | None:
        if self.duration_ts is not None and self.time_base is not None:
            return Decimal(self.duration_ts * self.time_base.numerator) / Decimal(
                self.time_base.denominator
            )
        return self.duration


@dataclass(frozen=True, slots=True)
class AudioStreamInfo:
    index: int
    codec_name: str | None
    sample_rate: int | None
    channels: int | None
    duration: Decimal | None
    codec_long_name: str | None = None
    channel_layout: str | None = None
    time_base: Fraction | None = None
    duration_ts: int | None = None
    start_time: Decimal | None = None
    bitrate: int | None = None


@dataclass(frozen=True, slots=True)
class MediaInfo:
    path: Path
    duration: Decimal | None
    format_name: str | None
    size: int | None
    video_streams: tuple[VideoStreamInfo, ...]
    audio_streams: tuple[AudioStreamInfo, ...]
    format_long_name: str | None = None
    start_time: Decimal | None = None
    bitrate: int | None = None

    @property
    def file_size(self) -> int | None:
        return self.size

    @property
    def has_video(self) -> bool:
        return bool(self.video_streams)

    @property
    def has_audio(self) -> bool:
        return bool(self.audio_streams)

    @property
    def primary_video_stream(self) -> VideoStreamInfo | None:
        return self.video_streams[0] if self.video_streams else None

    @property
    def primary_audio_stream(self) -> AudioStreamInfo | None:
        return self.audio_streams[0] if self.audio_streams else None


CodecFamily = Literal["h264", "hevc", "av1"]
EncoderBackend = Literal["nvenc", "qsv", "amf", "cpu"]


@dataclass(frozen=True, slots=True)
class EncoderCapability:
    name: str
    codec: CodecFamily
    backend: EncoderBackend
    description: str

    @property
    def hardware(self) -> bool:
        return self.backend != "cpu"


@dataclass(frozen=True, slots=True)
class HardwareCapabilities:
    encoders: tuple[EncoderCapability, ...]

    @property
    def available_encoders(self) -> tuple[EncoderCapability, ...]:
        return self.encoders

    @property
    def has_nvenc(self) -> bool:
        return any(item.backend == "nvenc" for item in self.encoders)

    @property
    def has_qsv(self) -> bool:
        return any(item.backend == "qsv" for item in self.encoders)

    @property
    def has_amf(self) -> bool:
        return any(item.backend == "amf" for item in self.encoders)

    def encoders_for(
        self, codec: CodecFamily, backend: EncoderBackend | None = None
    ) -> tuple[EncoderCapability, ...]:
        return tuple(
            item
            for item in self.encoders
            if item.codec == codec and (backend is None or item.backend == backend)
        )

    def supports(self, codec: CodecFamily, backend: EncoderBackend | None = None) -> bool:
        return bool(self.encoders_for(codec, backend))
