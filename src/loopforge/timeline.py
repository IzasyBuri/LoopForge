from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction


@dataclass(frozen=True, slots=True)
class LoopSettings:
    fps: Fraction
    source_duration: Fraction
    target_duration: Fraction | None = None
    repeat_count: int | None = None
    counted_frame_count: int | None = None
    frame_count: int | None = None
    stream_index: int = 0

    def __post_init__(self) -> None:
        if self.fps <= 0:
            raise ValueError("fps must be positive")
        if self.source_duration <= 0:
            raise ValueError("source_duration must be positive")
        if self.target_duration is not None and self.repeat_count is not None:
            raise ValueError("target_duration and repeat_count are mutually exclusive")
        if self.target_duration is None and self.repeat_count is None:
            raise ValueError("target_duration or repeat_count is required")
        if self.target_duration is not None and self.target_duration <= 0:
            raise ValueError("target_duration must be positive")
        if isinstance(self.repeat_count, bool):
            raise ValueError("repeat_count must be an integer")
        if self.repeat_count is not None and self.repeat_count <= 0:
            raise ValueError("repeat_count must be positive")
        if self.counted_frame_count is not None and self.counted_frame_count <= 0:
            raise ValueError("counted_frame_count must be positive")
        if self.frame_count is not None and self.frame_count <= 0:
            raise ValueError("frame_count must be positive")
        if self.stream_index < 0:
            raise ValueError("stream_index must be non-negative")


@dataclass(frozen=True, slots=True)
class Timeline:
    fps: Fraction
    source_frame_count: int
    output_frame_count: int
    stream_index: int

    @property
    def duration(self) -> Fraction:
        return Fraction(self.output_frame_count, 1) / self.fps


@dataclass(frozen=True, slots=True)
class RenderSegment:
    source_start_frame: int
    output_start_frame: int
    frame_count: int
    repeat_count: int = 1

    def __post_init__(self) -> None:
        if self.source_start_frame < 0 or self.output_start_frame < 0:
            raise ValueError("segment frame starts must be non-negative")
        if self.frame_count <= 0 or self.repeat_count <= 0:
            raise ValueError("segment counts must be positive")


@dataclass(frozen=True, slots=True)
class RenderPlan:
    timeline: Timeline
    segments: tuple[RenderSegment, ...]

    def __post_init__(self) -> None:
        output_start = 0
        output_frames = 0
        for segment in self.segments:
            if segment.output_start_frame != output_start:
                raise ValueError("segments must be contiguous")
            if segment.source_start_frame + segment.frame_count > self.timeline.source_frame_count:
                raise ValueError("segment exceeds source frame count")
            output_frames += segment.frame_count * segment.repeat_count
            output_start = output_frames
        if output_frames != self.timeline.output_frame_count:
            raise ValueError("segments must cover output frame count")

    @property
    def fps(self) -> Fraction:
        return self.timeline.fps

    @property
    def frame_count(self) -> int:
        return self.timeline.output_frame_count

    @property
    def duration(self) -> Fraction:
        return self.timeline.duration

    @property
    def stream_index(self) -> int:
        return self.timeline.stream_index


class TimelineEngine:
    @staticmethod
    def source_frame_count(settings: LoopSettings) -> int:
        if settings.counted_frame_count is not None:
            return settings.counted_frame_count
        if settings.frame_count is not None:
            return settings.frame_count
        exact = settings.source_duration * settings.fps
        if exact.denominator != 1:
            raise ValueError("source frame count is not exact")
        if exact.numerator <= 0:
            raise ValueError("source has no frames")
        return exact.numerator

    def create(self, settings: LoopSettings) -> Timeline:
        source_frames = self.source_frame_count(settings)
        if settings.repeat_count is not None:
            output_frames = source_frames * settings.repeat_count
        else:
            assert settings.target_duration is not None
            output_frames = (settings.target_duration * settings.fps).numerator // (
                settings.target_duration * settings.fps
            ).denominator
            if output_frames == 0:
                raise ValueError("target duration is shorter than one frame")
        return Timeline(settings.fps, source_frames, output_frames, settings.stream_index)


class VideoLoopEngine:
    def __init__(self, timeline_engine: TimelineEngine | None = None) -> None:
        self.timeline_engine = timeline_engine or TimelineEngine()

    def plan(self, settings: LoopSettings) -> RenderPlan:
        timeline = self.timeline_engine.create(settings)
        full, remainder = divmod(timeline.output_frame_count, timeline.source_frame_count)
        segments: list[RenderSegment] = []
        if full:
            segments.append(RenderSegment(0, 0, timeline.source_frame_count, full))
        if remainder:
            segments.append(
                RenderSegment(
                    0,
                    full * timeline.source_frame_count,
                    remainder,
                )
            )
        return RenderPlan(timeline, tuple(segments))

    def for_repeat_count(
        self,
        *,
        fps: Fraction,
        source_duration: Fraction,
        repeat_count: int,
        counted_frame_count: int | None = None,
        frame_count: int | None = None,
        stream_index: int = 0,
    ) -> RenderPlan:
        return self.plan(
            LoopSettings(
                fps,
                source_duration,
                repeat_count=repeat_count,
                counted_frame_count=counted_frame_count,
                frame_count=frame_count,
                stream_index=stream_index,
            )
        )

    def for_target_duration(
        self,
        *,
        fps: Fraction,
        source_duration: Fraction,
        target_duration: Fraction,
        counted_frame_count: int | None = None,
        frame_count: int | None = None,
        stream_index: int = 0,
    ) -> RenderPlan:
        return self.plan(
            LoopSettings(
                fps,
                source_duration,
                target_duration=target_duration,
                counted_frame_count=counted_frame_count,
                frame_count=frame_count,
                stream_index=stream_index,
            )
        )
