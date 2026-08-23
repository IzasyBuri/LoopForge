from fractions import Fraction
from pathlib import Path
from typing import cast

from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtWidgets import QApplication

from loopforge.playlist import Playlist, Track
from loopforge.playlist_widget import PlaylistPage


class FakeProbeController(QObject):
    succeeded = Signal(str, object)
    failed = Signal(str, str)

    def __init__(self) -> None:
        super().__init__()
        self.paths: list[Path] = []

    def probe(self, path: Path) -> str:
        self.paths.append(path)
        return str(len(self.paths))


def app() -> QApplication:
    current = QApplication.instance()
    return QApplication([]) if current is None else cast(QApplication, current)


def make_track(name: str) -> Track:
    return Track(name, Path(f"C:/{name}.wav"), name, name, 0, Fraction(2), 48000, 2)


def test_navigation_acceptance_rejection_and_order(tmp_path: Path) -> None:
    app()
    controller = FakeProbeController()
    page = PlaylistPage(cast(object, controller))  # type: ignore[arg-type]
    first = tmp_path / "a.wav"
    second = tmp_path / "b.wav"
    first.touch()
    second.touch()
    page.ingest_paths([first, tmp_path, second])
    assert controller.paths == [first.resolve(), second.resolve()]
    assert page._requests == {"1": 0, "2": 1}
    page.close()


def test_direct_slots_order_and_actions() -> None:
    app()
    page = PlaylistPage(None)
    page.playlist = Playlist((make_track("a"), make_track("b")))
    page.refresh()
    page.list.setCurrentRow(1)
    assert page.player.source() == QUrl.fromLocalFile("C:/b.wav")
    page.up()
    assert [track.id for track in page.playlist.tracks] == ["b", "a"]
    page.duplicate()
    assert [track.title for track in page.playlist.tracks] == ["b", "b", "a"]
    page.remove()
    assert [track.id for track in page.playlist.tracks] == ["b", "a"]
    page.list.setCurrentRow(0)
    page.toggle_play()
    assert page.play_button.text() == "Pause"
    page.close()
