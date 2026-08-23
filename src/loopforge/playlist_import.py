from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

MAX_PLAYLIST_BYTES = 2 * 1024 * 1024
MAX_PLAYLIST_ENTRIES = 10_000
SUPPORTED_PLAYLIST_SUFFIXES = frozenset({".m3u", ".m3u8", ".pls", ".txt"})


@dataclass(frozen=True, slots=True)
class PlaylistImportIssue:
    line: int | None
    entry: str | None
    message: str


@dataclass(frozen=True, slots=True)
class PlaylistImportResult:
    paths: tuple[Path, ...]
    issues: tuple[PlaylistImportIssue, ...]


def import_playlist(path: Path | str) -> PlaylistImportResult:
    source = Path(path).expanduser()
    if source.suffix.lower() not in SUPPORTED_PLAYLIST_SUFFIXES:
        return PlaylistImportResult(
            (), (PlaylistImportIssue(None, None, "Unsupported playlist format"),)
        )
    try:
        size = source.stat().st_size
    except OSError as error:
        return PlaylistImportResult((), (PlaylistImportIssue(None, None, str(error)),))
    if size > MAX_PLAYLIST_BYTES:
        return PlaylistImportResult((), (PlaylistImportIssue(None, None, "Playlist is too large"),))
    try:
        text = source.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as error:
        return PlaylistImportResult((), (PlaylistImportIssue(None, None, str(error)),))
    entries = _pls_entries(text) if source.suffix.lower() == ".pls" else _line_entries(text)
    paths: list[Path] = []
    issues: list[PlaylistImportIssue] = []
    for line, entry in entries[:MAX_PLAYLIST_ENTRIES]:
        parsed = urlsplit(entry)
        if (parsed.scheme and not re.match(r"^[A-Za-z]:[\\/]", entry)) or entry.startswith("//"):
            issues.append(PlaylistImportIssue(line, entry, "Nonlocal entries are not supported"))
            continue
        candidate = Path(entry).expanduser()
        if not candidate.is_absolute():
            candidate = source.parent / candidate
        if candidate.suffix.lower() in SUPPORTED_PLAYLIST_SUFFIXES:
            issues.append(PlaylistImportIssue(line, entry, "Nested playlists are not supported"))
        elif not candidate.is_file():
            issues.append(PlaylistImportIssue(line, entry, "File does not exist"))
        else:
            paths.append(candidate.resolve())
    if len(entries) > MAX_PLAYLIST_ENTRIES:
        issues.append(PlaylistImportIssue(None, None, "Playlist entry limit exceeded"))
    return PlaylistImportResult(tuple(paths), tuple(issues))


def _line_entries(text: str) -> list[tuple[int, str]]:
    return [
        (line, value)
        for line, raw in enumerate(text.splitlines(), 1)
        if (value := raw.strip()) and not value.startswith(("#", ";"))
    ]


def _pls_entries(text: str) -> list[tuple[int, str]]:
    values: list[tuple[int, int, str]] = []
    for line, raw in enumerate(text.splitlines(), 1):
        match = re.match(r"\s*File(\d+)\s*=\s*(.*?)\s*$", raw, re.IGNORECASE)
        if match and match.group(2):
            values.append((int(match.group(1)), line, match.group(2)))
    values.sort(key=lambda value: value[0])
    return [(line, value) for _, line, value in values]
