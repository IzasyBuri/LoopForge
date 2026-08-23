from dataclasses import FrozenInstanceError
from fractions import Fraction

import pytest

from loopforge.timeline import LoopSettings, TimelineEngine, VideoLoopEngine


def settings(**values: object) -> LoopSettings:
    defaults: dict[str, object] = {
        "fps": Fraction(30),
        "source_duration": Fraction(1),
        "target_duration": Fraction(1),
    }
    defaults.update(values)
    return LoopSettings(**defaults)  # type: ignore[arg-type]


def test_settings_are_frozen_slotted_and_validated() -> None:
    value = settings()
    with pytest.raises(FrozenInstanceError):
        value.fps = Fraction(60)  # type: ignore[misc]
    assert not hasattr(value, "__dict__")
    for values in (
        {"fps": Fraction(0)},
        {"source_duration": Fraction(0)},
        {"target_duration": Fraction(0)},
        {"target_duration": None, "repeat_count": 0},
        {"repeat_count": 2},
        {"target_duration": None, "repeat_count": True},
        {"stream_index": -1},
    ):
        with pytest.raises(ValueError):
            settings(**values)


def test_source_frame_count_priority_and_exact_fallback() -> None:
    engine = TimelineEngine()
    assert engine.source_frame_count(settings(counted_frame_count=31, frame_count=30)) == 31
    assert engine.source_frame_count(settings(frame_count=30)) == 30
    assert engine.source_frame_count(settings()) == 30
    with pytest.raises(ValueError, match="not exact"):
        engine.source_frame_count(settings(fps=Fraction(30000, 1001)))


@pytest.mark.parametrize(
    ("fps", "target", "frames"),
    [
        (Fraction(30), Fraction(1, 30), 1),
        (Fraction(30), Fraction(1), 30),
        (Fraction(30000, 1001), Fraction(1), 29),
        (Fraction(60), Fraction(1), 60),
        (Fraction(60000, 1001), Fraction(1), 59),
        (Fraction(30), Fraction(61, 60), 30),
    ],
)
def test_cfr_grid_floors_without_frame_beyond_target(
    fps: Fraction, target: Fraction, frames: int
) -> None:
    plan = VideoLoopEngine().for_target_duration(
        fps=fps,
        source_duration=Fraction(1001, 30000) if fps.denominator != 1 else Fraction(1),
        target_duration=target,
        counted_frame_count=1 if fps.denominator != 1 else None,
    )
    assert plan.frame_count == frames
    assert plan.duration <= target
    assert plan.duration + 1 / fps > target


def test_target_shorter_than_frame_rejected_and_equal_accepted() -> None:
    engine = VideoLoopEngine()
    with pytest.raises(ValueError, match="shorter than one frame"):
        engine.for_target_duration(
            fps=Fraction(30), source_duration=Fraction(1), target_duration=Fraction(1, 31)
        )
    assert (
        engine.for_target_duration(
            fps=Fraction(30), source_duration=Fraction(1), target_duration=Fraction(1, 30)
        ).frame_count
        == 1
    )


def test_short_equal_long_targets_and_compact_huge_plan() -> None:
    engine = VideoLoopEngine()
    assert (
        engine.for_target_duration(
            fps=Fraction(30), source_duration=Fraction(1), target_duration=Fraction(1, 2)
        ).frame_count
        == 15
    )
    assert (
        engine.for_target_duration(
            fps=Fraction(30), source_duration=Fraction(1), target_duration=Fraction(1)
        ).frame_count
        == 30
    )
    plan = engine.for_target_duration(
        fps=Fraction(30), source_duration=Fraction(1), target_duration=Fraction(10**12)
    )
    assert plan.frame_count == 30 * 10**12
    assert len(plan.segments) == 1
    assert plan.segments[0].repeat_count == 10**12


def test_repeat_count_and_partial_tail_preserve_stream_index() -> None:
    engine = VideoLoopEngine()
    repeated = engine.for_repeat_count(
        fps=Fraction(30), source_duration=Fraction(1), repeat_count=10**12, stream_index=4
    )
    assert repeated.frame_count == 30 * 10**12
    assert repeated.stream_index == 4
    assert len(repeated.segments) == 1
    partial = engine.for_target_duration(
        fps=Fraction(30), source_duration=Fraction(1), target_duration=Fraction(7, 3)
    )
    assert [(item.frame_count, item.repeat_count) for item in partial.segments] == [
        (30, 2),
        (10, 1),
    ]
