from pathlib import Path

import pytest

from loopforge.playlist_import import import_playlist


@pytest.mark.parametrize("suffix", [".m3u", ".m3u8", ".txt"])
def test_line_formats_unicode_relative_missing_and_url(tmp_path: Path, suffix: str) -> None:
    media = tmp_path / "音 ü.wav"
    media.touch()
    source = tmp_path / f"list{suffix}"
    source.write_text(
        f"#EXTM3U\n{media.name}\nmissing.wav\nhttps://example.com/a.wav\n", encoding="utf-8-sig"
    )
    result = import_playlist(source)
    assert result.paths == (media.resolve(),)
    assert [(issue.line, issue.message) for issue in result.issues] == [
        (3, "File does not exist"),
        (4, "Nonlocal entries are not supported"),
    ]


def test_numeric_pls_order_and_absolute_path(tmp_path: Path) -> None:
    first = tmp_path / "first.wav"
    second = tmp_path / "second.wav"
    first.touch()
    second.touch()
    source = tmp_path / "list.pls"
    source.write_text(f"[playlist]\nFile10={second}\nTitle1=x\nFile2={first}\n", encoding="utf-8")
    assert import_playlist(source).paths == (first.resolve(), second.resolve())


def test_unsupported_nested_and_missing_source(tmp_path: Path) -> None:
    assert import_playlist(tmp_path / "none.m3u").issues
    bad = tmp_path / "list.json"
    bad.touch()
    assert import_playlist(bad).issues[0].message == "Unsupported playlist format"
    nested = tmp_path / "nested.m3u"
    nested.touch()
    source = tmp_path / "outer.m3u"
    source.write_text(nested.name, encoding="utf-8")
    assert import_playlist(source).issues[0].message == "Nested playlists are not supported"
