from __future__ import annotations

import unicodedata
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from itertools import islice
from pathlib import Path
from uuid import uuid4

from .models import MediaInfo

TrackIdFactory = Callable[[], str]


class TrackError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Track:
    id: str
    path: Path
    title: str
    original_title: str
    stream_index: int
    duration: Fraction
    sample_rate: int | None
    channels: int | None

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise TrackError("Track ID cannot be empty")
        if not self.title.strip() or not self.original_title.strip():
            raise TrackError("Track title cannot be empty")
        if self.stream_index < 0:
            raise TrackError("Stream index cannot be negative")
        if self.duration <= 0:
            raise TrackError("Track duration must be positive")
        if self.sample_rate is not None and self.sample_rate <= 0:
            raise TrackError("Sample rate must be positive")
        if self.channels is not None and self.channels <= 0:
            raise TrackError("Channel count must be positive")

    @property
    def display_title(self) -> str:
        return self.title

    def renamed(self, title: str) -> Track:
        value = title.strip()
        if not value:
            raise TrackError("Track title cannot be empty")
        return replace(self, title=value)


@dataclass(frozen=True, slots=True)
class Playlist:
    tracks: tuple[Track, ...] = ()

    def __post_init__(self) -> None:
        ids = [track.id for track in self.tracks]
        if len(ids) != len(set(ids)):
            raise TrackError("Playlist track IDs must be unique")


@dataclass(frozen=True, slots=True)
class RenderSegment:
    track_id: str
    path: Path
    stream_index: int
    output_start: Fraction
    source_start: Fraction
    duration: Fraction
    clipped: bool

    def __post_init__(self) -> None:
        if not self.track_id or self.stream_index < 0:
            raise ValueError("Invalid render source")
        if self.output_start < 0 or self.source_start < 0 or self.duration <= 0:
            raise ValueError("Invalid render segment timing")

    @property
    def output_end(self) -> Fraction:
        return self.output_start + self.duration


@dataclass(frozen=True, slots=True)
class PlaylistTimelineEntry:
    track_id: str
    title: str
    path: Path
    stream_index: int
    output_start: Fraction
    source_start: Fraction
    duration: Fraction
    clipped: bool

    def __post_init__(self) -> None:
        if not self.track_id or self.stream_index < 0:
            raise ValueError("Invalid timeline source")
        if self.output_start < 0 or self.source_start < 0 or self.duration <= 0:
            raise ValueError("Invalid timeline entry timing")

    @property
    def output_end(self) -> Fraction:
        return self.output_start + self.duration


@dataclass(frozen=True, slots=True)
class TimelinePreview:
    text: str
    truncated: bool


@dataclass(frozen=True, slots=True)
class AudioRenderPlan:
    playlist: Playlist
    full_repetitions: int
    tail: tuple[RenderSegment, ...]
    duration: Fraction

    def __post_init__(self) -> None:
        if self.full_repetitions < 0 or self.duration <= 0 or not self.playlist.tracks:
            raise ValueError("Invalid audio render plan")
        playlist_duration = PlaylistEngine.total_duration(self.playlist)
        expected = playlist_duration * self.full_repetitions
        for index, segment in enumerate(self.tail):
            track = self.playlist.tracks[index]
            if segment.track_id != track.id or segment.path != track.path:
                raise ValueError("Tail source does not match playlist")
            if segment.stream_index != track.stream_index or segment.source_start != 0:
                raise ValueError("Tail source does not match track")
            if segment.output_start != expected:
                raise ValueError("Tail segments must be contiguous")
            if segment.duration > track.duration:
                raise ValueError("Tail segment exceeds track")
            if segment.clipped != (segment.duration < track.duration):
                raise ValueError("Invalid clipped state")
            if segment.clipped and index != len(self.tail) - 1:
                raise ValueError("Only final segment may be clipped")
            expected = segment.output_end
        if expected != self.duration:
            raise ValueError("Segments must cover exact duration")

    @property
    def empty(self) -> bool:
        return False

    @property
    def timeline_entry_count(self) -> int:
        return self.full_repetitions * len(self.playlist.tracks) + len(self.tail)

    def iter_timeline(self) -> Iterator[PlaylistTimelineEntry]:
        total = PlaylistEngine.total_duration(self.playlist)
        for repetition in range(self.full_repetitions):
            output_start = repetition * total
            for track in self.playlist.tracks:
                yield PlaylistTimelineEntry(
                    track.id,
                    track.title,
                    track.path,
                    track.stream_index,
                    output_start,
                    Fraction(),
                    track.duration,
                    False,
                )
                output_start += track.duration
        titles = {track.id: track.title for track in self.playlist.tracks}
        for segment in self.tail:
            yield PlaylistTimelineEntry(
                segment.track_id,
                titles[segment.track_id],
                segment.path,
                segment.stream_index,
                segment.output_start,
                segment.source_start,
                segment.duration,
                segment.clipped,
            )


def format_chapter_timestamp(value: Fraction) -> str:
    if value < 0:
        raise ValueError("Chapter timestamp cannot be negative")
    seconds = value.numerator // value.denominator
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours:02}:{minutes:02}:{seconds:02}"
    return f"{minutes:02}:{seconds:02}"


def sanitize_chapter_title(title: str) -> str:
    cleaned = "".join(
        " " if unicodedata.category(character) == "Cc" else character for character in title
    )
    return " ".join(cleaned.split())


def _fallback_chapter_title(path: Path) -> str:
    return sanitize_chapter_title(path.stem) or "Untitled"


def format_youtube_chapters(
    entries: Iterable[PlaylistTimelineEntry], limit: int | None = None
) -> str:
    if limit is not None and limit < 0:
        raise ValueError("Limit cannot be negative")
    source = entries if limit is None else islice(entries, limit)
    return "\n".join(
        f"{format_chapter_timestamp(entry.output_start)} "
        f"{sanitize_chapter_title(entry.title) or _fallback_chapter_title(entry.path)}"
        for entry in source
    )


def preview_youtube_chapters(
    entries: Iterable[PlaylistTimelineEntry], limit: int
) -> TimelinePreview:
    if limit < 0:
        raise ValueError("Limit cannot be negative")
    items = list(islice(entries, limit + 1))
    return TimelinePreview(format_youtube_chapters(items[:limit]), len(items) > limit)


def parse_target_duration(text: str) -> Fraction:
    parts = text.strip().split(":")
    if not 1 <= len(parts) <= 3 or any(not part for part in parts):
        raise ValueError("Use seconds, MM:SS, or HH:MM:SS")
    try:
        values = [Decimal(part) for part in parts]
    except InvalidOperation as error:
        raise ValueError("Use seconds, MM:SS, or HH:MM:SS") from error
    if any(not value.is_finite() or value < 0 for value in values):
        raise ValueError("Duration must be positive")
    if len(values) > 1 and any(value != value.to_integral() for value in values[:-1]):
        raise ValueError("Only seconds may be fractional")
    if len(values) > 1 and values[-1] >= 60:
        raise ValueError("Seconds must be below 60")
    if len(values) == 3 and values[1] >= 60:
        raise ValueError("Minutes must be below 60")
    seconds = sum(
        (Fraction(value) * (60**index) for index, value in enumerate(reversed(values))),
        Fraction(),
    )
    if seconds <= 0:
        raise ValueError("Duration must be positive")
    return seconds


class PlaylistEngine:
    def __init__(self, id_factory: TrackIdFactory = lambda: uuid4().hex) -> None:
        self._id_factory = id_factory

    def add(self, playlist: Playlist, track: Track, index: int | None = None) -> Playlist:
        if any(item.id == track.id for item in playlist.tracks):
            raise TrackError(f"Duplicate track ID: {track.id}")
        items = list(playlist.tracks)
        position = len(items) if index is None else index
        if not 0 <= position <= len(items):
            raise IndexError(position)
        items.insert(position, track)
        return Playlist(tuple(items))

    def remove(self, playlist: Playlist, track_id: str) -> Playlist:
        index = self._index(playlist, track_id)
        return Playlist(playlist.tracks[:index] + playlist.tracks[index + 1 :])

    def reorder(self, playlist: Playlist, track_ids: tuple[str, ...]) -> Playlist:
        current = {track.id: track for track in playlist.tracks}
        if len(track_ids) != len(current) or set(track_ids) != set(current):
            raise TrackError("Reorder IDs must match playlist occurrences")
        return Playlist(tuple(current[track_id] for track_id in track_ids))

    def move(self, playlist: Playlist, track_id: str, index: int) -> Playlist:
        source = self._index(playlist, track_id)
        if not 0 <= index < len(playlist.tracks):
            raise IndexError(index)
        items = list(playlist.tracks)
        items.insert(index, items.pop(source))
        return Playlist(tuple(items))

    def duplicate(self, playlist: Playlist, track_id: str, index: int | None = None) -> Playlist:
        source = self._index(playlist, track_id)
        duplicate = replace(playlist.tracks[source], id=self._id_factory())
        return self.add(playlist, duplicate, source + 1 if index is None else index)

    def rename(self, playlist: Playlist, track_id: str, title: str) -> Playlist:
        index = self._index(playlist, track_id)
        items = list(playlist.tracks)
        items[index] = items[index].renamed(title)
        return Playlist(tuple(items))

    @staticmethod
    def total_duration(playlist: Playlist) -> Fraction:
        return sum((track.duration for track in playlist.tracks), Fraction())

    def render_repetitions(self, playlist: Playlist, repeat_count: int) -> AudioRenderPlan:
        if isinstance(repeat_count, bool) or not isinstance(repeat_count, int) or repeat_count <= 0:
            raise ValueError("Repeat count must be a positive integer")
        if not playlist.tracks:
            raise TrackError("Cannot repeat empty playlist")
        return AudioRenderPlan(
            playlist, repeat_count, (), self.total_duration(playlist) * repeat_count
        )

    def render_target(self, playlist: Playlist, target: Fraction) -> AudioRenderPlan:
        if isinstance(target, bool) or target <= 0:
            raise ValueError("Target duration must be positive")
        if not playlist.tracks:
            raise TrackError("Cannot render empty playlist")
        total = self.total_duration(playlist)
        repetitions, remaining = divmod(Fraction(target), total)
        output_start = repetitions * total
        tail: list[RenderSegment] = []
        for track in playlist.tracks:
            if remaining == 0:
                break
            duration = min(track.duration, remaining)
            tail.append(
                RenderSegment(
                    track.id,
                    track.path,
                    track.stream_index,
                    output_start,
                    Fraction(),
                    duration,
                    duration < track.duration,
                )
            )
            output_start += duration
            remaining -= duration
        return AudioRenderPlan(playlist, repetitions, tuple(tail), Fraction(target))

    @staticmethod
    def _index(playlist: Playlist, track_id: str) -> int:
        for index, track in enumerate(playlist.tracks):
            if track.id == track_id:
                return index
        raise KeyError(track_id)


def _positive_fraction(value: Decimal | None) -> Fraction | None:
    if value is None or not value.is_finite() or value <= 0:
        return None
    return Fraction(value)


def track_from_media(info: MediaInfo, id_factory: TrackIdFactory = lambda: uuid4().hex) -> Track:
    path = info.path.expanduser().resolve()
    if not path.is_file():
        raise TrackError("Track path must be a local regular file")
    stream = info.primary_audio_stream
    if stream is None:
        raise TrackError("Media has no audio stream")
    duration: Fraction | None = None
    if stream.duration_ts is not None and stream.time_base is not None:
        duration = stream.duration_ts * stream.time_base
        if duration <= 0:
            duration = None
    if duration is None:
        duration = _positive_fraction(stream.duration)
    if duration is None:
        duration = _positive_fraction(info.duration)
    if duration is None:
        raise TrackError("Audio duration is unknown or nonpositive")
    return Track(
        id=id_factory(),
        path=path,
        title=path.stem,
        original_title=path.stem,
        stream_index=stream.index,
        duration=duration,
        sample_rate=stream.sample_rate,
        channels=stream.channels,
    )
