from decimal import Decimal
from fractions import Fraction
from pathlib import Path

import pytest

from loopforge.media_probe import MediaProbeError
from loopforge.models import MediaInfo, VideoStreamInfo
from loopforge.output_validation import OutputValidationError, OutputValidationService
from loopforge.timeline import RenderPlan, VideoLoopEngine


class FakeProbe:
    def __init__(self, info: MediaInfo | None = None, error: MediaProbeError | None = None) -> None:
        self.info = info
        self.error = error

    def probe(
        self, path: Path | str, timeout: float | None = 30, *, count_frames: bool = False
    ) -> MediaInfo:
        assert count_frames
        if self.error is not None:
            raise self.error
        assert self.info is not None
        return self.info


def plan() -> RenderPlan:
    return VideoLoopEngine().for_target_duration(
        fps=Fraction(30000, 1001),
        source_duration=Fraction(1001, 30000),
        target_duration=Fraction(1001, 1000),
        counted_frame_count=1,
        stream_index=7,
    )


def info(
    path: Path,
    *,
    frames: int | None = 30,
    fps: Fraction | None = Fraction(30000, 1001),
    duration: Decimal | None = None,
    duration_ts: int | None = 90090,
    time_base: Fraction | None = Fraction(1, 90000),
    index: int = 0,
) -> MediaInfo:
    stream = VideoStreamInfo(
        index=index,
        codec_name="ffv1",
        width=16,
        height=16,
        frame_rate=fps,
        duration=duration,
        counted_frame_count=frames,
        duration_ts=duration_ts,
        time_base=time_base,
    )
    return MediaInfo(path, duration, "matroska", 1, (stream,), ())


def validate(media: MediaInfo, render_plan: RenderPlan | None = None):  # type: ignore[no-untyped-def]
    return OutputValidationService(FakeProbe(media)).validate(media.path, render_plan or plan())  # type: ignore[arg-type]


def test_exact_pass_and_typed_result_fields(tmp_path: Path) -> None:
    path = tmp_path / "out.mkv"
    result = validate(info(path))
    assert result.valid and result.errors == ()
    assert (result.path, result.expected_frame_count, result.actual_frame_count) == (path, 30, 30)
    assert (result.expected_fps, result.actual_fps) == (Fraction(30000, 1001),) * 2
    assert (result.expected_duration, result.actual_duration) == (Fraction(1001, 1000),) * 2


@pytest.mark.parametrize(
    ("values", "message"),
    [
        ({"frames": 31}, "frame count is 31, expected 30"),
        ({"frames": 29}, "frame count is 29, expected 30"),
        ({"fps": Fraction(30)}, "frame rate is 30, expected 30000/1001"),
        ({"frames": None}, "counted frame count is missing"),
    ],
)
def test_strict_failures(tmp_path: Path, values: dict[str, object], message: str) -> None:
    result = validate(info(tmp_path / "out.mkv", **values))  # type: ignore[arg-type]
    assert not result.valid and message in result.errors


def test_no_video_and_operational_error(tmp_path: Path) -> None:
    path = tmp_path / "out.mkv"
    empty = MediaInfo(path, None, "matroska", 1, (), ())
    result = validate(empty)
    assert not result.valid and result.errors == ("required video stream is missing",)
    with pytest.raises(OutputValidationError, match="probe broke"):
        OutputValidationService(FakeProbe(error=MediaProbeError("probe broke"))).validate(  # type: ignore[arg-type]
            path, plan()
        )


def test_source_index_remap_uses_primary_output_stream(tmp_path: Path) -> None:
    assert validate(info(tmp_path / "out.mkv", index=0)).valid


def test_fractional_duration_fallback_and_one_time_base_tolerance(tmp_path: Path) -> None:
    render_plan = VideoLoopEngine().for_target_duration(
        fps=Fraction(3), source_duration=Fraction(1, 3), target_duration=Fraction(1, 3)
    )
    path = tmp_path / "out.mkv"
    exact = validate(
        info(
            path,
            frames=1,
            fps=Fraction(3),
            duration=Decimal("0.3333333333333333333333333333"),
            duration_ts=None,
            time_base=Fraction(1, 10**28),
        ),
        render_plan,
    )
    assert exact.actual_duration == Fraction(Decimal("0.3333333333333333333333333333"))
    assert exact.valid
    tolerance = validate(
        info(path, frames=1, fps=Fraction(3), duration_ts=4, time_base=Fraction(1, 12)),
        render_plan,
    )
    assert tolerance.valid
