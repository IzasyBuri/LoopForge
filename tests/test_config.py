from pathlib import Path

from loopforge.config import ConfigStore, Settings


def test_config_round_trip_and_atomic_cleanup(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "settings.json"
    store = ConfigStore(path)
    expected = Settings("C:/tools/ffmpeg.exe", "C:/tools/ffprobe.exe")

    store.save(expected)

    assert store.load() == expected
    assert list(path.parent.glob("*.tmp")) == []


def test_invalid_config_returns_defaults(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_text("not json", encoding="utf-8")

    assert ConfigStore(path).load() == Settings()
