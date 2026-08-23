from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction
from pathlib import Path

from .media_probe import MediaProbeError, MediaProbeService
from .timeline import RenderPlan


class OutputValidationError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class OutputValidationResult:
    valid: bool
    errors: tuple[str, ...]
    path: Path
    expected_frame_count: int
    actual_frame_count: int | None
    expected_fps: Fraction
    actual_fps: Fraction | None
    expected_duration: Fraction
    actual_duration: Fraction | None
    expected_audio_codec: str | None = None
    actual_audio_codec: str | None = None
    actual_audio_duration: Fraction | None = None


class OutputValidationService:
    def __init__(self, probe: MediaProbeService) -> None:
        self.probe = probe

    def validate(
        self,
        path: Path | str,
        plan: RenderPlan,
        timeout: float | None = 30,
        *,
        require_audio: bool = False,
        audio_codec: str | None = None,
    ) -> OutputValidationResult:
        try:
            info = self.probe.probe(path, timeout=timeout, count_frames=True)
        except MediaProbeError as error:
            raise OutputValidationError(str(error)) from error
        stream = info.primary_video_stream
        errors: list[str] = []
        actual_frame_count = stream.counted_frame_count if stream is not None else None
        actual_fps = stream.frame_rate if stream is not None else None
        actual_duration: Fraction | None = None
        if stream is None:
            errors.append("required video stream is missing")
        else:
            if stream.duration_ts is not None and stream.time_base is not None:
                actual_duration = Fraction(stream.duration_ts) * stream.time_base
            elif stream.duration is not None:
                actual_duration = Fraction(Decimal(stream.duration))
            if actual_frame_count is None:
                errors.append("counted frame count is missing")
            elif actual_frame_count != plan.frame_count:
                errors.append(f"frame count is {actual_frame_count}, expected {plan.frame_count}")
            if actual_fps != plan.fps:
                errors.append(f"frame rate is {actual_fps}, expected {plan.fps}")
            if actual_duration is None:
                errors.append("stream duration is missing")
            elif stream.time_base is None:
                errors.append("stream time base is missing")
            elif abs(actual_duration - plan.duration) > stream.time_base:
                errors.append(f"duration is {actual_duration}, expected {plan.duration}")
        audio = info.primary_audio_stream
        actual_audio_duration: Fraction | None = None
        if require_audio and audio is None:
            errors.append("required audio stream is missing")
        if audio is not None:
            expected_codec = "opus" if audio_codec in {"opus", "libopus"} else audio_codec
            if expected_codec is not None and audio.codec_name != expected_codec:
                errors.append(f"audio codec is {audio.codec_name}, expected {expected_codec}")
            if require_audio and (audio.sample_rate is None or audio.sample_rate <= 0):
                errors.append("audio sample rate is missing or nonpositive")
            if require_audio and (audio.channels is None or audio.channels <= 0):
                errors.append("audio channel count is missing or nonpositive")
            if audio.duration_ts is not None and audio.time_base is not None:
                actual_audio_duration = Fraction(audio.duration_ts) * audio.time_base
            elif audio.duration is not None:
                actual_audio_duration = Fraction(audio.duration)
            tolerance = audio.time_base or Fraction()
            if audio.sample_rate:
                samples = 1024 if audio.codec_name == "aac" else 960
                tolerance = max(tolerance, Fraction(samples, audio.sample_rate))
            if actual_audio_duration is None:
                errors.append("audio duration is missing")
            elif abs(actual_audio_duration - plan.duration) > tolerance:
                errors.append(
                    f"audio duration is {actual_audio_duration}, expected {plan.duration}"
                )
        return OutputValidationResult(
            not errors,
            tuple(errors),
            info.path,
            plan.frame_count,
            actual_frame_count,
            plan.fps,
            actual_fps,
            plan.duration,
            actual_duration,
            audio_codec,
            audio.codec_name if audio is not None else None,
            actual_audio_duration,
        )
