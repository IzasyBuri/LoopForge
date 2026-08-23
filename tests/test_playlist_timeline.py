from collections.abc import Iterator
from fractions import Fraction
from itertools import islice
from pathlib import Path

import pytest

from loopforge.playlist import (
    Playlist,
    PlaylistEngine,
    PlaylistTimelineEntry,
    Track,
    format_chapter_timestamp,
    format_youtube_chapters,
    parse_target_duration,
    preview_youtube_chapters,
)


def track(name: str, duration: int, title: str | None = None) -> Track:
    return Track(name, Path(f"{name}.wav"), title or name, name, 0, Fraction(duration), 1, 1)


def entry(start: Fraction, title: str = "A", path: str = "A.wav") -> PlaylistTimelineEntry:
    return PlaylistTimelineEntry("id", title, Path(path), 0, start, Fraction(), Fraction(1), False)


def test_exact_timeline_chapters_clipping_and_boundary() -> None:
    playlist = Playlist((track("A", 222), track("B", 255), track("C", 321)))
    plan = PlaylistEngine().render_target(playlist, Fraction(1500))
    timeline = list(plan.iter_timeline())
    assert [item.output_start for item in timeline] == [0, 222, 477, 798, 1020, 1275]
    assert format_youtube_chapters(timeline) == "\n".join(
        ("00:00 A", "03:42 B", "07:57 C", "13:18 A", "17:00 B", "21:15 C")
    )
    assert timeline[-1].clipped and timeline[-1].output_end == 1500
    exact = PlaylistEngine().render_target(playlist, Fraction(1596))
    assert exact.full_repetitions == 2 and exact.tail == ()


def test_fractional_floor_and_timestamp_boundaries() -> None:
    assert [
        format_chapter_timestamp(Fraction(value)) for value in (59, 60, 3599, 3600, 86400, 360000)
    ] == ["00:59", "01:00", "59:59", "01:00:00", "24:00:00", "100:00:00"]
    assert format_chapter_timestamp(Fraction(719, 10)) == "01:11"


def test_titles_sanitization_duplicates_unicode_and_fallback() -> None:
    items = [entry(Fraction(), "同名\n\t\x00"), entry(Fraction(1), "同名")]
    assert format_youtube_chapters(items) == "00:00 同名\n00:01 同名"
    assert (
        format_youtube_chapters([entry(Fraction(), "\n\t\x00", "fallback.wav")]) == "00:00 fallback"
    )


def test_parse_target_duration_forms_and_errors() -> None:
    assert [parse_target_duration(value) for value in ("12.5", "02:03.5", "1:02:03.25")] == [
        Fraction(25, 2),
        Fraction(247, 2),
        Fraction(14893, 4),
    ]
    for value in ("", "0", "x", "1::2", "1.5:02", "1:60", "1:60:00", "-1", "nan", "inf"):
        with pytest.raises(ValueError):
            parse_target_duration(value)


def test_huge_plan_is_compact_lazy_and_preview_bounded() -> None:
    plan = PlaylistEngine().render_target(Playlist((track("A", 1),)), Fraction(10**12))
    assert plan.timeline_entry_count == 10**12 and plan.tail == ()
    assert [item.output_start for item in islice(plan.iter_timeline(), 3)] == [0, 1, 2]
    consumed = 0

    def source() -> Iterator[PlaylistTimelineEntry]:
        nonlocal consumed
        while True:
            consumed += 1
            yield entry(Fraction(consumed - 1))

    preview = preview_youtube_chapters(source(), 3)
    assert preview.truncated and preview.text == "00:00 A\n00:01 A\n00:02 A" and consumed == 4


def test_timeline_entry_validation() -> None:
    with pytest.raises(ValueError):
        PlaylistTimelineEntry("", "A", Path("A"), 0, Fraction(), Fraction(), Fraction(1), False)
    with pytest.raises(ValueError):
        PlaylistTimelineEntry("A", "A", Path("A"), 0, Fraction(-1), Fraction(), Fraction(1), False)
