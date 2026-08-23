from pathlib import Path

from loopforge.config import ConfigStore
from loopforge.lifecycle import Lifecycle
from loopforge.media_tools import DiscoveryError, MediaTools


def test_lifecycle_starts_and_stops_without_media_tools(tmp_path: Path) -> None:
    def unavailable(*_: object) -> MediaTools:
        raise DiscoveryError("missing")

    lifecycle = Lifecycle(
        ConfigStore(tmp_path / "settings.json"),
        tmp_path,
        tmp_path / "missing",
        unavailable,
    )

    runtime = lifecycle.startup()

    assert runtime.media_tools is None
    assert lifecycle.runtime is runtime
    lifecycle.shutdown()
    assert lifecycle.runtime is None
    assert (tmp_path / "settings.json").is_file()
    assert (tmp_path / "logs" / "loopforge.log").is_file()
