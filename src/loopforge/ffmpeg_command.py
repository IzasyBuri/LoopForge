from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

from .encoding import EncoderSelection, EncodingSettings
from .timeline import RenderPlan


@dataclass(frozen=True, slots=True)
class FFmpegCommandBuilder:
    def build(
        self,
        source: Path | str,
        output: Path | str,
        plan: RenderPlan,
        settings: EncodingSettings,
        encoder: EncoderSelection,
        *,
        overwrite: bool = False,
        prepared_audio: Path | str | None = None,
    ) -> tuple[str, ...]:
        settings.validate_output(Path(output).suffix)
        fps = _fraction(plan.fps)
        audio_input = () if prepared_audio is None else ("-i", str(prepared_audio))
        audio_output = ("-an",) if prepared_audio is None else ("-map", "1:a:0", "-c:a", "copy")
        faststart = ("-movflags", "+faststart") if settings.container == "mp4" else ()
        return (
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-y" if overwrite else "-n",
            "-stream_loop",
            "-1",
            "-i",
            str(source),
            *audio_input,
            "-map",
            f"0:{plan.stream_index}",
            *audio_output,
            "-vf",
            f"trim=start_frame=0:end_frame={plan.frame_count},setpts=N/({fps}*TB)",
            "-frames:v",
            str(plan.frame_count),
            "-c:v",
            encoder.name,
            *_encoder_options(encoder.name, settings.speed, settings.quality),
            "-pix_fmt",
            "yuv420p",
            "-progress",
            "pipe:1",
            "-nostats",
            *faststart,
            "-f",
            settings.container,
            str(output),
        )


def _encoder_options(name: str, speed: str, quality: int) -> tuple[str, ...]:
    intent = {"ultrafast": 0, "fast": 1, "medium": 2, "slow": 3}.get(speed, 2)
    if name in {"libx264", "libx265"}:
        return "-preset", speed, "-crf", str(quality)
    if name == "libsvtav1":
        return "-preset", str((12, 8, 6, 4)[intent]), "-crf", str(quality)
    if name == "libaom-av1":
        return "-cpu-used", str((8, 6, 4, 2)[intent]), "-crf", str(quality), "-b:v", "0"
    if name == "librav1e":
        return "-speed", str((10, 8, 6, 4)[intent]), "-qp", str(quality)
    if name.endswith("_nvenc"):
        return "-preset", ("p1", "p3", "p5", "p7")[intent], "-cq", str(quality), "-b:v", "0"
    if name.endswith("_qsv"):
        return (
            "-preset",
            ("veryfast", "faster", "medium", "slow")[intent],
            "-global_quality",
            str(quality),
        )
    if name.endswith("_amf"):
        return (
            "-quality",
            ("speed", "balanced", "balanced", "quality")[intent],
            "-qp_i",
            str(quality),
            "-qp_p",
            str(quality),
        )
    raise ValueError(f"unsupported encoder: {name}")


def _fraction(value: Fraction) -> str:
    return f"{value.numerator}/{value.denominator}"
