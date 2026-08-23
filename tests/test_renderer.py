from __future__ import annotations

from fractions import Fraction
from pathlib import Path
from typing import Any, Literal, cast

import pytest

from loopforge.encoding import EncoderSelection, EncodingSettings
from loopforge.encoding_engine import EncodingResult
from loopforge.models import HardwareCapabilities
from loopforge.playlist import Playlist, PlaylistEngine, Track
from loopforge.renderer import RenderRequest, RenderStageError, VideoMusicRenderer, _sample_count
from loopforge.timeline import VideoLoopEngine


class FakeFFmpeg:
    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []

    def run(self, args: Any, *, timeout: float | None = None) -> None:
        command = tuple(args)
        self.calls.append(command)
        output = Path(command[-1])
        if output.suffix == ".s16le":
            samples = int(
                next(value for value in command if "atrim=end_sample=" in value)
                .split("atrim=end_sample=")[1]
                .split(",")[0]
            )
            source = Path(command[command.index("-i") + 1])
            if "-stream_loop" in command:
                data = source.read_bytes()
                output.write_bytes((data * (samples * 4 // len(data) + 1))[: samples * 4])
            else:
                output.write_bytes(bytes([len(self.calls)]) * samples * 4)
        else:
            output.write_bytes(b"prepared")


class FakeEngine:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.prepared: Path | None = None

    def render(
        self,
        source: Path,
        output: Path,
        *args: Any,
        prepared_audio: Path | None = None,
        **kwargs: Any,
    ) -> EncodingResult:
        self.prepared = prepared_audio
        if self.fail:
            raise RuntimeError("broken")
        output.write_bytes(b"rendered")
        return EncodingResult(output, EncoderSelection("libx264", "cpu"), (), False)


def track(path: Path, identity: str, duration: Fraction = Fraction(1, 100)) -> Track:
    path.touch(exist_ok=True)
    return Track(identity, path, identity, identity, 0, duration, 8000, 1)


def plans(path: Path, tracks: tuple[Track, ...], target: Fraction) -> tuple[Any, Any]:
    video = VideoLoopEngine().for_target_duration(
        fps=Fraction(100), source_duration=Fraction(1, 100), target_duration=target
    )
    audio = PlaylistEngine().render_target(Playlist(tracks), target)
    return video, audio


def settings(audio: Literal["aac", "opus", "none"] = "aac") -> EncodingSettings:
    return EncodingSettings("test", "h264", audio, "mp4", "cpu", 20, "fast")


def capabilities() -> HardwareCapabilities:
    from loopforge.models import AudioEncoderCapability, EncoderCapability

    return HardwareCapabilities(
        (EncoderCapability("libx264", "h264", "cpu", "test"),),
        (AudioEncoderCapability("aac", "aac", "test"),),
    )


def test_sample_count_uses_floor_arithmetic() -> None:
    assert _sample_count(Fraction(1, 3)) == 16000
    assert _sample_count(Fraction(1, 48001)) == 0


def test_render_request_rejects_duration_mismatch(tmp_path: Path) -> None:
    item = track(tmp_path / "a.wav", "a")
    video, _ = plans(tmp_path, (item,), Fraction(1, 50))
    audio = PlaylistEngine().render_target(Playlist((item,)), Fraction(1, 100))
    with pytest.raises(ValueError, match="durations must match"):
        RenderRequest(
            tmp_path / "source", tmp_path / "out.mp4", video, audio, settings(), capabilities()
        )


@pytest.mark.parametrize(
    "audio_plan,codec,message",
    [(True, "none", "requires an audio codec"), (False, "aac", "requires an audio plan")],
)
def test_render_request_rejects_audio_setting_mismatch(
    tmp_path: Path, audio_plan: bool, codec: str, message: str
) -> None:
    item = track(tmp_path / "a.wav", "a")
    video, audio = plans(tmp_path, (item,), Fraction(1, 100))
    with pytest.raises(ValueError, match=message):
        RenderRequest(
            tmp_path / "source",
            tmp_path / "out.mp4",
            video,
            audio if audio_plan else None,
            settings(cast(Literal["aac", "opus", "none"], codec)),
            capabilities(),
        )


def test_prepare_audio_normalizes_duplicate_once_and_cycles_in_order(tmp_path: Path) -> None:
    fake = FakeFFmpeg()
    renderer = VideoMusicRenderer(fake, FakeEngine())  # type: ignore[arg-type]
    shared = tmp_path / "same.wav"
    other = tmp_path / "other.wav"
    tracks = (track(shared, "a"), track(other, "b"), track(shared, "c"))
    _, plan = plans(tmp_path, tracks, Fraction(3, 100))
    renderer._prepare_audio(plan, "aac", tmp_path, None)
    assert len(fake.calls) == 3
    size = _sample_count(Fraction(1, 100)) * 4
    assert (tmp_path / "cycle.s16le").read_bytes() == bytes([1]) * size + bytes([2]) * size + bytes(
        [1]
    ) * size
    assert f"atrim=end_sample={_sample_count(plan.duration)},asetpts=N/SR/TB" in fake.calls[-1]


def test_huge_repetitions_do_not_iterate_timeline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeFFmpeg()
    renderer = VideoMusicRenderer(fake, FakeEngine())  # type: ignore[arg-type]
    item = track(tmp_path / "a.wav", "a")
    plan = PlaylistEngine().render_repetitions(Playlist((item,)), 10**20)
    monkeypatch.setattr(
        type(plan), "iter_timeline", lambda self: (_ for _ in ()).throw(AssertionError())
    )
    renderer._prepare_audio(plan, "aac", tmp_path, None)
    assert len(fake.calls) == 2


def test_render_maps_prepared_audio_and_publishes(tmp_path: Path) -> None:
    source = tmp_path / "source.mkv"
    source.touch()
    item = track(tmp_path / "a.wav", "a")
    video, audio = plans(tmp_path, (item,), Fraction(1, 100))
    engine = FakeEngine()
    output = tmp_path / "out.mp4"
    result = VideoMusicRenderer(cast(Any, FakeFFmpeg()), cast(Any, engine)).render(
        RenderRequest(source, output, video, audio, settings(), capabilities())
    )
    assert result.path == output and output.read_bytes() == b"rendered"
    assert engine.prepared is not None and engine.prepared.name == "audio.m4a"
    assert not list(tmp_path.glob(".loopforge-*"))


def test_failure_cleans_workspace_and_preserves_target(tmp_path: Path) -> None:
    source = tmp_path / "source.mkv"
    source.touch()
    output = tmp_path / "out.mp4"
    output.write_bytes(b"keep")
    item = track(tmp_path / "a.wav", "a")
    video, audio = plans(tmp_path, (item,), Fraction(1, 100))
    request = RenderRequest(
        source, output, video, audio, settings(), capabilities(), overwrite=True
    )
    with pytest.raises(RenderStageError, match="video: broken"):
        VideoMusicRenderer(FakeFFmpeg(), FakeEngine(True)).render(request)  # type: ignore[arg-type]
    assert output.read_bytes() == b"keep"
    assert not list(tmp_path.glob(".loopforge-*"))
