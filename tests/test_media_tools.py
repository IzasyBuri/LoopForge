import subprocess
from pathlib import Path

import pytest

from loopforge.config import Settings
from loopforge.media_tools import DiscoveryError, discover_media_tools


def test_discovery_uses_configured_and_bundled_binaries(tmp_path: Path) -> None:
    ffmpeg = tmp_path / "custom-ffmpeg"
    ffprobe = tmp_path / "ffprobe.exe"
    ffmpeg.touch()
    ffprobe.touch()

    def run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        name = "ffmpeg" if Path(command[0]) == ffmpeg else "ffprobe"
        return subprocess.CompletedProcess(command, 0, f"{name} version 7.0\n", "")

    tools = discover_media_tools(Settings(str(ffmpeg)), tmp_path, run)

    assert tools.ffmpeg == ffmpeg.resolve()
    assert tools.ffprobe == ffprobe.resolve()
    assert tools.ffmpeg_version == "ffmpeg version 7.0"


def test_discovery_rejects_invalid_binary(tmp_path: Path) -> None:
    binary = tmp_path / "ffmpeg"
    binary.touch()

    def run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 1, "", "bad")

    with pytest.raises(DiscoveryError):
        discover_media_tools(Settings(str(binary)), tmp_path / "missing", run)
