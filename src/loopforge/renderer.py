from __future__ import annotations

import os
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from tempfile import TemporaryDirectory

from .encoding import EncodingCompatibilityError, EncodingSettings
from .encoding_engine import EncodingEngine, EncodingResult
from .ffmpeg_service import FFmpegService
from .models import HardwareCapabilities
from .playlist import AudioRenderPlan
from .timeline import RenderPlan


@dataclass(frozen=True, slots=True)
class RenderRequest:
    source: Path
    output: Path
    video_plan: RenderPlan
    audio_plan: AudioRenderPlan | None
    settings: EncodingSettings
    capabilities: HardwareCapabilities
    overwrite: bool = False

    def __post_init__(self) -> None:
        if self.audio_plan is not None:
            if not self.audio_plan.playlist.tracks:
                raise ValueError("Playlist cannot be empty")
            if self.audio_plan.duration != self.video_plan.duration:
                raise ValueError("Audio and video plan durations must match exactly")
            for track in self.audio_plan.playlist.tracks:
                if _sample_count(track.duration) < 1:
                    raise ValueError("Track duration must contain at least one output sample")
                if not track.path.is_file():
                    raise FileNotFoundError(track.path)
        if self.settings.audio_codec == "none" and self.audio_plan is not None:
            raise ValueError("Audio plan requires an audio codec")
        if self.settings.audio_codec != "none" and self.audio_plan is None:
            raise ValueError("Audio codec requires an audio plan")


class RenderStageError(RuntimeError):
    def __init__(self, stage: str, message: str) -> None:
        super().__init__(f"{stage}: {message}")
        self.stage = stage


class VideoMusicRenderer:
    def __init__(self, ffmpeg: FFmpegService, engine: EncodingEngine) -> None:
        self.ffmpeg = ffmpeg
        self.engine = engine

    def render(self, request: RenderRequest, *, timeout: float | None = None) -> EncodingResult:
        request.settings.validate_output(request.output.suffix)
        if request.output.exists() and not request.overwrite:
            raise FileExistsError(request.output)
        if not request.output.parent.is_dir():
            raise FileNotFoundError(request.output.parent)
        codec = request.settings.audio_codec
        if codec != "none" and not request.capabilities.supports_audio(codec):
            raise EncodingCompatibilityError(f"no {codec} encoder is advertised")
        with TemporaryDirectory(prefix=".loopforge-", dir=request.output.parent) as name:
            workspace = Path(name)
            prepared = None
            if request.audio_plan is not None:
                try:
                    prepared = self._prepare_audio(request.audio_plan, codec, workspace, timeout)
                except Exception as error:
                    raise RenderStageError("audio", str(error)) from error
            staged = workspace / request.output.name
            try:
                result = self.engine.render(
                    request.source,
                    staged,
                    request.video_plan,
                    request.settings,
                    request.capabilities,
                    overwrite=True,
                    timeout=timeout,
                    prepared_audio=prepared,
                )
            except Exception as error:
                raise RenderStageError("video", str(error)) from error
            os.replace(staged, request.output)
            return EncodingResult(
                request.output, result.encoder, result.attempts, result.hardware_fallback
            )

    def _prepare_audio(
        self,
        plan: AudioRenderPlan,
        codec: str,
        workspace: Path,
        timeout: float | None,
    ) -> Path:
        normalized: dict[tuple[Path, int, int], Path] = {}
        cycle = workspace / "cycle.s16le"
        with cycle.open("wb") as destination:
            for track in plan.playlist.tracks:
                samples = _sample_count(track.duration)
                key = (track.path, track.stream_index, samples)
                raw = normalized.get(key)
                if raw is None:
                    raw = workspace / f"track-{len(normalized)}.s16le"
                    self.ffmpeg.run(
                        (
                            "-hide_banner",
                            "-loglevel",
                            "error",
                            "-nostdin",
                            "-y",
                            "-i",
                            str(track.path),
                            "-map",
                            f"0:{track.stream_index}",
                            "-vn",
                            "-af",
                            f"aresample=48000,apad,atrim=end_sample={samples},asetpts=N/SR/TB",
                            "-f",
                            "s16le",
                            "-acodec",
                            "pcm_s16le",
                            "-ar",
                            "48000",
                            "-ac",
                            "2",
                            str(raw),
                        ),
                        timeout=timeout,
                    )
                    size = raw.stat().st_size
                    expected = samples * 4
                    if size != expected:
                        raise ValueError(
                            f"normalized track size is {size} bytes, expected {expected}"
                        )
                    normalized[key] = raw
                with raw.open("rb") as source:
                    while block := source.read(1024 * 1024):
                        destination.write(block)
        if cycle.stat().st_size == 0:
            raise ValueError("Normalized playlist cycle cannot be empty")
        extension = ".m4a" if codec == "aac" else ".mka"
        output = workspace / f"audio{extension}"
        encoder = "aac" if codec == "aac" else "libopus"
        samples = _sample_count(plan.duration)
        self.ffmpeg.run(
            (
                "-hide_banner",
                "-loglevel",
                "error",
                "-nostdin",
                "-y",
                "-stream_loop",
                "-1",
                "-f",
                "s16le",
                "-ar",
                "48000",
                "-ac",
                "2",
                "-i",
                str(cycle),
                "-map",
                "0:a:0",
                "-af",
                f"atrim=end_sample={samples},asetpts=N/SR/TB",
                "-c:a",
                encoder,
                "-b:a",
                "192k",
                str(output),
            ),
            timeout=timeout,
        )
        return output


def _sample_count(value: Fraction) -> int:
    return value.numerator * 48000 // value.denominator
