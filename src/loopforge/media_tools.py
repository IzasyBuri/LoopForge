from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .config import Settings

Runner = Callable[..., subprocess.CompletedProcess[str]]


@dataclass(frozen=True, slots=True)
class MediaTools:
    ffmpeg: Path
    ffprobe: Path
    ffmpeg_version: str
    ffprobe_version: str


class DiscoveryError(RuntimeError):
    pass


def discover_media_tools(
    settings: Settings,
    bundled_dir: Path | None = None,
    runner: Runner = subprocess.run,
) -> MediaTools:
    ffmpeg = _discover("ffmpeg", settings.ffmpeg_path, bundled_dir, runner)
    ffprobe = _discover("ffprobe", settings.ffprobe_path, bundled_dir, runner)
    return MediaTools(ffmpeg[0], ffprobe[0], ffmpeg[1], ffprobe[1])


def _discover(
    name: str, configured: str | None, bundled_dir: Path | None, runner: Runner
) -> tuple[Path, str]:
    candidates: list[Path] = []
    if configured:
        candidates.append(Path(configured))
    executable = f"{name}.exe" if os.name == "nt" else name
    if bundled_dir:
        candidates.append(bundled_dir / executable)
    found = shutil.which(name)
    if found:
        candidates.append(Path(found))
    for candidate in candidates:
        if not candidate.is_file():
            continue
        try:
            result = runner(
                [str(candidate), "-version"], capture_output=True, text=True, timeout=5, check=False
            )
        except (OSError, subprocess.SubprocessError):
            continue
        first_line = (result.stdout or result.stderr).splitlines()
        if (
            result.returncode == 0
            and first_line
            and first_line[0].lower().startswith(f"{name} version")
        ):
            return candidate.resolve(), first_line[0]
    raise DiscoveryError(f"Valid {name} binary not found")
