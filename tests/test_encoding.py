from fractions import Fraction
from pathlib import Path

import pytest

from loopforge.encoding import (
    BALANCED,
    CUSTOM,
    FAST,
    MAXIMUM_QUALITY,
    PRESETS,
    QUALITY,
    ULTRA_FAST,
    EncoderSelection,
    EncodingCompatibilityError,
    EncodingSettings,
    select_encoder,
)
from loopforge.ffmpeg_command import FFmpegCommandBuilder
from loopforge.models import EncoderCapability, HardwareCapabilities
from loopforge.timeline import VideoLoopEngine


def capabilities(*names: str) -> HardwareCapabilities:
    values = []
    for name in names:
        codec = (
            "hevc" if name.startswith(("hevc", "libx265")) else "av1" if "av1" in name else "h264"
        )
        backend = next((item for item in ("nvenc", "qsv", "amf") if item in name), "cpu")
        values.append(EncoderCapability(name, codec, backend, ""))  # type: ignore[arg-type]
    return HardwareCapabilities(tuple(values))


def test_presets_are_typed_frozen_and_complete() -> None:
    assert PRESETS == (ULTRA_FAST, FAST, BALANCED, QUALITY, MAXIMUM_QUALITY, CUSTOM)
    assert [item.name for item in PRESETS] == [
        "Ultra Fast",
        "Fast",
        "Balanced",
        "Quality",
        "Maximum Quality",
        "Custom",
    ]
    with pytest.raises(AttributeError):
        BALANCED.quality = 1  # type: ignore[misc]


def test_custom_validation_and_container_policy() -> None:
    with pytest.raises(ValueError, match="Opus audio requires MKV"):
        EncodingSettings("Custom", "h264", "opus", "mp4", "cpu", 20, "slow")
    assert EncodingSettings("Custom", "h264", "opus", "mkv", "cpu", 20, "slow")
    assert EncodingSettings("Custom", "h264", "aac", "mp4", "cpu", 20, "slow")
    with pytest.raises(ValueError, match="quality"):
        EncodingSettings("Custom", "h264", "none", "mkv", "cpu", 64, "slow")
    with pytest.raises(ValueError, match="extension"):
        BALANCED.validate_output(".mkv")


def test_selection_is_deterministic_strict_and_uses_exact_cpu_fallbacks() -> None:
    available = capabilities("h264_amf", "h264_qsv", "h264_nvenc", "libx264")
    assert select_encoder(BALANCED, available).name == "h264_nvenc"
    explicit = EncodingSettings("Custom", "h264", "none", "mp4", "qsv", 20, "slow")
    assert select_encoder(explicit, available).name == "h264_qsv"
    with pytest.raises(EncodingCompatibilityError):
        select_encoder(explicit, capabilities("libx264"))
    assert select_encoder(BALANCED, capabilities("libx264")).name == "libx264"
    assert select_encoder(MAXIMUM_QUALITY, capabilities("librav1e")).name == "librav1e"


def test_command_has_constant_size_safe_argv_and_exact_frame_math(tmp_path: Path) -> None:
    plan = VideoLoopEngine().for_target_duration(
        fps=Fraction(30000, 1001),
        source_duration=Fraction(1001, 30000) * 7,
        target_duration=Fraction(1001, 30000) * 100_000,
        counted_frame_count=7,
        stream_index=3,
    )
    source = tmp_path / "source [ä 中].mkv"
    output = tmp_path / "output [ä 中].mp4"
    args = FFmpegCommandBuilder().build(
        source, output, plan, BALANCED, select_encoder(BALANCED, capabilities("libx264"))
    )
    assert len(args) < 40
    assert args[args.index("-stream_loop") + 1] == "-1"
    assert args[args.index("-map") + 1] == "0:3"
    assert args[args.index("-frames:v") + 1] == "100000"
    assert args[args.index("-vf") + 1] == (
        "trim=start_frame=0:end_frame=100000,setpts=N/(30000/1001*TB)"
    )
    assert "-nostdin" in args and "-r" not in args
    assert args[-1] == str(output)
    assert "-progress" in args and args[args.index("-f") + 1] == "mp4"


@pytest.mark.parametrize(
    ("encoder", "expected"),
    [
        ("libx264", ("-preset", "slow", "-crf", "20")),
        ("libx265", ("-preset", "slow", "-crf", "20")),
        ("libsvtav1", ("-preset", "4", "-crf", "20")),
        ("libaom-av1", ("-cpu-used", "2", "-crf", "20", "-b:v", "0")),
        ("librav1e", ("-speed", "4", "-qp", "20")),
        ("h264_nvenc", ("-preset", "p7", "-cq", "20", "-b:v", "0")),
        ("h264_qsv", ("-preset", "slow", "-global_quality", "20")),
        ("h264_amf", ("-quality", "quality", "-qp_i", "20", "-qp_p", "20")),
    ],
)
def test_encoder_specific_options(encoder: str, expected: tuple[str, ...], tmp_path: Path) -> None:
    plan = VideoLoopEngine().for_repeat_count(
        fps=Fraction(24), source_duration=Fraction(1, 24), repeat_count=1
    )
    settings = EncodingSettings("Custom", "h264", "none", "mp4", "cpu", 20, "slow")
    args = FFmpegCommandBuilder().build(
        tmp_path / "input.mkv",
        tmp_path / "output.mp4",
        plan,
        settings,
        EncoderSelection(
            encoder, next((item for item in ("nvenc", "qsv", "amf") if item in encoder), "cpu")
        ),
    )
    start = args.index("-c:v") + 2
    assert args[start : start + len(expected)] == expected
