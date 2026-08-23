from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .metadata import APP_NAME


@dataclass(slots=True)
class Settings:
    ffmpeg_path: str | None = None
    ffprobe_path: str | None = None


class ConfigStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_config_path()

    def load(self) -> Settings:
        try:
            data: Any = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return Settings()
        if not isinstance(data, dict):
            return Settings()
        return Settings(
            ffmpeg_path=_optional_string(data.get("ffmpeg_path")),
            ffprobe_path=_optional_string(data.get("ffprobe_path")),
        )

    def save(self, settings: Settings) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(
            dir=self.path.parent, prefix=".settings-", suffix=".tmp"
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(asdict(settings), stream, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise


def default_data_dir() -> Path:
    root = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
    return (Path(root) if root else Path.home() / "AppData" / "Local") / APP_NAME


def default_config_path() -> Path:
    return default_data_dir() / "settings.json"


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None
