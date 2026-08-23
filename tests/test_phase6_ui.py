from fractions import Fraction
from pathlib import Path
from typing import cast

from PySide6.QtWidgets import QApplication

from loopforge.playlist import Playlist, PlaylistEngine, Track
from loopforge.playlist_widget import PlaylistPage


def app() -> QApplication:
    current = QApplication.instance()
    return QApplication([]) if current is None else cast(QApplication, current)


def track(name: str, duration: Fraction = Fraction(2), title: str | None = None) -> Track:
    return Track(name, Path(f"{name}.wav"), title or name, name, 0, duration, 1, 1)


def page_with(*tracks: Track) -> PlaylistPage:
    page = PlaylistPage(None)
    page.playlist = Playlist(tracks)
    page.refresh()
    return page


def test_empty_invalid_disabled_and_accessibility() -> None:
    app()
    page = page_with()
    assert not page.copy_timestamps_button.isEnabled()
    assert page.copy_timestamps_button.accessibleName() == "Copy YouTube timestamps"
    page.playlist = Playlist((track("A"),))
    page.target_duration.setText("invalid")
    assert not page.copy_timestamps_button.isEnabled() and "seconds" in page.timeline_status.text()
    page.close()


def test_valid_preview_copy_and_arbitrary_hours() -> None:
    application = app()
    page = page_with(
        track("A", Fraction(222)), track("B", Fraction(255)), track("C", Fraction(321))
    )
    page.target_duration.setText("00:25:00")
    expected = "\n".join(("00:00 A", "03:42 B", "07:57 C", "13:18 A", "17:00 B", "21:15 C"))
    assert page.timeline_preview.toPlainText() == expected
    page.copy_timestamps()
    assert application.clipboard().text() == expected
    page.target_duration.setText("100:00:00")
    assert page.copy_timestamps_button.isEnabled()
    page.close()


def test_reorder_rename_duplicate_refresh_and_duplicate_warning() -> None:
    app()
    page = page_with(track("A", Fraction(1, 2)), track("B", Fraction(1, 2)))
    page.target_duration.setText("2")
    assert "duplicate formatted timestamp" in page.timeline_status.text()
    page.playlist = page.engine.reorder(page.playlist, ("B", "A"))
    page.playlist = page.engine.rename(page.playlist, "A", "Renamed")
    page.engine = PlaylistEngine(iter(("copy",)).__next__)
    page.playlist = page.engine.duplicate(page.playlist, "B")
    page.refresh()
    assert page.timeline_preview.toPlainText().splitlines()[:3] == [
        "00:00 B",
        "00:00 B",
        "00:01 Renamed",
    ]
    page.close()


def test_preview_truncation_and_copy_cap_refuses_before_iteration() -> None:
    app()
    page = page_with(track("A", Fraction(1)))
    page.target_duration.setText("10001")
    assert len(page.timeline_preview.toPlainText().splitlines()) == 200
    assert "Preview limited to 200 of 10001 lines" in page.timeline_status.text()
    assert "Cannot copy 10001 lines" in page.timeline_status.text()
    assert not page.copy_timestamps_button.isEnabled()
    page.copy_timestamps()
    assert "Cannot copy 10001 lines" in page.timeline_status.text()
    page.close()
