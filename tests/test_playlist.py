from decimal import Decimal
from fractions import Fraction
from pathlib import Path

import pytest

from loopforge.models import AudioStreamInfo, MediaInfo
from loopforge.playlist import Playlist, PlaylistEngine, Track, TrackError, track_from_media


def track(name: str, duration: int = 2) -> Track:
    return Track(name, Path(f"{name}.wav"), name, name, 0, Fraction(duration), 48000, 2)


def test_operations_invariants_and_exact_planning() -> None:
    engine = PlaylistEngine(iter(("copy",)).__next__)
    playlist = engine.add(engine.add(Playlist(), track("a")), track("b", 3))
    assert [item.id for item in engine.move(playlist, "b", 0).tracks] == ["b", "a"]
    assert engine.rename(playlist, "a", " New ").tracks[0].title == "New"
    assert engine.duplicate(playlist, "a").tracks[1].id == "copy"
    assert engine.remove(playlist, "a").tracks == (track("b", 3),)
    assert engine.reorder(playlist, ("b", "a")).tracks == (track("b", 3), track("a"))
    plan = engine.render_target(playlist, Fraction(12, 1))
    assert plan.full_repetitions == 2
    assert plan.duration == 12
    assert [
        (part.track_id, part.output_start, part.duration, part.clipped) for part in plan.tail
    ] == [("a", Fraction(10), Fraction(2), False)]
    assert engine.render_repetitions(playlist, 3).duration == 15
    with pytest.raises(TrackError):
        Playlist((track("a"), track("a")))
    with pytest.raises(TrackError):
        engine.reorder(playlist, ("a",))


def test_track_from_media_duration_precedence(tmp_path: Path) -> None:
    path = tmp_path / "sound.wav"
    path.touch()
    stream = AudioStreamInfo(
        2, "pcm", 48000, 2, Decimal("7"), time_base=Fraction(1, 4), duration_ts=9
    )
    info = MediaInfo(path, Decimal("8"), "wav", 1, (), (stream,))
    assert track_from_media(info, lambda: "id").duration == Fraction(9, 4)
    stream = AudioStreamInfo(2, "pcm", 48000, 2, Decimal("7"))
    assert track_from_media(MediaInfo(path, Decimal("8"), "wav", 1, (), (stream,))).duration == 7
    stream = AudioStreamInfo(2, "pcm", 48000, 2, None)
    assert track_from_media(MediaInfo(path, Decimal("8"), "wav", 1, (), (stream,))).duration == 8
