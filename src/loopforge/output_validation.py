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


class OutputValidationService:
    def __init__(self, probe: MediaProbeService) -> None:
        self.probe = probe

    def validate(
        self, path: Path | str, plan: RenderPlan, timeout: float | None = 30
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
        )
