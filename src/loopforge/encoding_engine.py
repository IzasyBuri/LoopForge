from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .encoding import EncoderSelection, EncodingSettings, select_encoder
from .ffmpeg_command import FFmpegCommandBuilder
from .ffmpeg_service import FFmpegError, FFmpegService
from .models import HardwareCapabilities, ProcessResult
from .output_validation import OutputValidationError, OutputValidationService
from .timeline import RenderPlan


@dataclass(frozen=True, slots=True)
class EncodingAttempt:
    encoder: EncoderSelection
    result: ProcessResult | None
    error: str


class EncodingError(RuntimeError):
    def __init__(self, message: str, attempts: tuple[EncodingAttempt, ...]) -> None:
        super().__init__(message)
        self.attempts = attempts


@dataclass(frozen=True, slots=True)
class EncodingResult:
    path: Path
    encoder: EncoderSelection
    attempts: tuple[EncodingAttempt, ...]
    hardware_fallback: bool


class EncodingEngine:
    def __init__(
        self,
        ffmpeg: FFmpegService,
        validator: OutputValidationService,
        builder: FFmpegCommandBuilder | None = None,
    ) -> None:
        self.ffmpeg = ffmpeg
        self.validator = validator
        self.builder = builder or FFmpegCommandBuilder()

    def render(
        self,
        source: Path | str,
        output: Path | str,
        plan: RenderPlan,
        settings: EncodingSettings,
        capabilities: HardwareCapabilities,
        *,
        overwrite: bool = False,
        timeout: float | None = None,
    ) -> EncodingResult:
        source_path = Path(source)
        target = Path(output)
        settings.validate_output(target.suffix)
        if not source_path.is_file():
            raise FileNotFoundError(source_path)
        if not target.parent.is_dir():
            raise FileNotFoundError(target.parent)
        if source_path.resolve() == target.resolve():
            raise ValueError("input and output must be different files")
        if target.exists() and not overwrite:
            raise FileExistsError(target)
        temporary = target.with_name(f".{target.stem}.loopforge-tmp{target.suffix}")
        if temporary.exists():
            temporary.unlink()
        selected = select_encoder(settings, capabilities)
        selections = [selected]
        if settings.backend == "auto" and selected.backend != "cpu":
            cpu_settings = EncodingSettings(
                settings.name,
                settings.video_codec,
                settings.audio_codec,
                settings.container,
                "cpu",
                settings.quality,
                settings.speed,
            )
            selections.append(select_encoder(cpu_settings, capabilities))
        attempts: list[EncodingAttempt] = []
        try:
            for encoder in selections:
                try:
                    result = self.ffmpeg.run(
                        self.builder.build(
                            source_path, temporary, plan, settings, encoder, overwrite=True
                        ),
                        timeout=timeout,
                    )
                except FFmpegError as error:
                    attempts.append(EncodingAttempt(encoder, error.result, str(error)))
                    if temporary.exists():
                        temporary.unlink()
                    continue
                validation = self.validator.validate(temporary, plan)
                if not validation.valid:
                    raise OutputValidationError("; ".join(validation.errors))
                attempts.append(EncodingAttempt(encoder, result, ""))
                os.replace(temporary, target)
                return EncodingResult(target, encoder, tuple(attempts), encoder != selected)
            raise EncodingError("encoding failed", tuple(attempts))
        finally:
            if temporary.exists():
                temporary.unlink()
