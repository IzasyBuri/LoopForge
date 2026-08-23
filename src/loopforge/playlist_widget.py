from __future__ import annotations

from itertools import islice
from pathlib import Path
from typing import cast

from PySide6.QtCore import Qt, QUrl, Slot
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QPixmap
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QPlainTextEdit,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from .models import MediaInfo
from .playlist import (
    Playlist,
    PlaylistEngine,
    Track,
    TrackError,
    format_chapter_timestamp,
    format_youtube_chapters,
    parse_target_duration,
    preview_youtube_chapters,
    track_from_media,
)
from .playlist_import import SUPPORTED_PLAYLIST_SUFFIXES, import_playlist
from .probe_tasks import ProbeController, extract_local_files
from .render_tasks import RenderController, UIRenderRequest
from .waveform import WaveformController, WaveformRequest


def format_duration(milliseconds: int) -> str:
    seconds = max(0, milliseconds) // 1000
    return f"{seconds // 60:02}:{seconds % 60:02}"


class PlaylistPage(QWidget):
    def __init__(
        self,
        controller: ProbeController | None,
        waveform_controller: WaveformController | None = None,
        render_controller: RenderController | None = None,
    ) -> None:
        super().__init__()
        self.controller = controller
        self.waveform_controller = waveform_controller
        self.render_controller = render_controller
        self._render_request: str | None = None
        self._background: MediaInfo | None = None
        self._closed = False
        self._waveform_request: str | None = None
        self._waveform_track: str | None = None
        self.setAcceptDrops(True)
        self.engine = PlaylistEngine()
        self.playlist = Playlist()
        self._requests: dict[str, int] = {}
        self._pending: list[tuple[Path, Track | str | None]] = []
        self.player = QMediaPlayer(self)
        self.audio_output = QAudioOutput(self)
        self.player.setAudioOutput(self.audio_output)
        self.audio_output.setVolume(1.0)
        self._build()
        if controller:
            controller.succeeded.connect(self._probe_succeeded)
            controller.failed.connect(self._probe_failed)
        if waveform_controller:
            waveform_controller.succeeded.connect(self._waveform_succeeded)
            waveform_controller.failed.connect(self._waveform_failed)
        if render_controller:
            render_controller.succeeded.connect(self._render_succeeded)
            render_controller.failed.connect(self._render_failed)
        self.player.positionChanged.connect(self._position_changed)
        self.player.durationChanged.connect(self._duration_changed)
        self.player.errorOccurred.connect(lambda _error, text: self.error_label.setText(text))

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        heading = QLabel("Playlist")
        heading.setObjectName("heading")
        layout.addWidget(heading)
        controls = QHBoxLayout()
        for text, slot in (
            ("Browse audio…", self.browse),
            ("Remove", self.remove),
            ("Up", self.up),
            ("Down", self.down),
            ("Duplicate", self.duplicate),
            ("Rename", self.rename),
        ):
            button = QPushButton(text)
            button.clicked.connect(slot)
            controls.addWidget(button)
        layout.addLayout(controls)
        self.list = QListWidget()
        self.list.setAccessibleName("Playlist tracks")
        self.list.currentRowChanged.connect(self.select)
        layout.addWidget(self.list, 1)
        self.waveform_label = QLabel("")
        self.waveform_label.setFixedHeight(96)
        self.waveform_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.waveform_label.setAccessibleName("Selected track waveform")
        layout.addWidget(self.waveform_label)
        self.total_label = QLabel("Total: 00:00")
        self.error_label = QLabel("")
        self.error_label.setWordWrap(True)
        layout.addWidget(self.total_label)
        layout.addWidget(self.error_label)
        target = QHBoxLayout()
        target.addWidget(QLabel("Target duration"))
        self.target_duration = QLineEdit()
        self.target_duration.setAccessibleName("Render target duration")
        self.target_duration.setPlaceholderText("HH:MM:SS, MM:SS, or seconds")
        self.target_duration.textChanged.connect(self.refresh_timeline)
        target.addWidget(self.target_duration, 1)
        self.copy_timestamps_button = QPushButton("Copy YouTube Timestamps")
        self.copy_timestamps_button.setAccessibleName("Copy YouTube timestamps")
        self.copy_timestamps_button.clicked.connect(self.copy_timestamps)
        target.addWidget(self.copy_timestamps_button)
        layout.addLayout(target)
        self.timeline_preview = QPlainTextEdit()
        self.timeline_preview.setReadOnly(True)
        self.timeline_preview.setAccessibleName("Timestamp preview")
        layout.addWidget(self.timeline_preview)
        self.timeline_status = QLabel("")
        self.timeline_status.setWordWrap(True)
        layout.addWidget(self.timeline_status)
        render = QHBoxLayout()
        self.background_label = QLabel("Background: none selected")
        self.background_label.setAccessibleName("Render video background")
        self.background_label.setWordWrap(True)
        render.addWidget(self.background_label)
        self.output_path = QLineEdit(str(Path.home() / "loopforge-output.mp4"))
        self.output_path.setAccessibleName("Render output path")
        self.output_path.textChanged.connect(self._refresh_render)
        render.addWidget(self.output_path, 1)
        self.output_browse_button = QPushButton("Browse Save…")
        self.output_browse_button.setAccessibleName("Browse render output path")
        self.output_browse_button.clicked.connect(self.browse_output)
        render.addWidget(self.output_browse_button)
        self.render_button = QPushButton("Render")
        self.render_button.setAccessibleName("Start video render")
        self.render_button.clicked.connect(self.start_render)
        render.addWidget(self.render_button)
        layout.addLayout(render)
        self.render_status = QLabel("Select a video background and add audio tracks")
        self.render_status.setAccessibleName("Render status")
        self.render_status.setWordWrap(True)
        layout.addWidget(self.render_status)
        playback = QHBoxLayout()
        self.play_button = QPushButton("Play")
        self.play_button.clicked.connect(self.toggle_play)
        playback.addWidget(self.play_button)
        self.seek = QSlider(Qt.Orientation.Horizontal)
        self.seek.sliderMoved.connect(self.player.setPosition)
        playback.addWidget(self.seek, 1)
        self.time_label = QLabel("00:00 / 00:00")
        playback.addWidget(self.time_label)
        volume = QSlider(Qt.Orientation.Horizontal)
        volume.setRange(0, 100)
        volume.setValue(100)
        volume.valueChanged.connect(lambda value: self.audio_output.setVolume(value / 100))
        playback.addWidget(volume)
        layout.addLayout(playback)
        self.refresh_timeline()

    def set_background(self, info: MediaInfo) -> None:
        self._background = info if info.primary_video_stream is not None else None
        self.background_label.setText(
            f"Background: {info.path.name} · Ready"
            if self._background
            else "Background: selected media has no video"
        )
        self._refresh_render()

    @Slot()
    def browse_output(self) -> None:
        name, _ = QFileDialog.getSaveFileName(
            self, "Save rendered video", self.output_path.text(), "MP4 video (*.mp4)"
        )
        if name:
            self.output_path.setText(name if name.lower().endswith(".mp4") else f"{name}.mp4")

    def _render_error(self) -> str | None:
        if self.render_controller is None:
            return "Rendering unavailable"
        if self._background is None:
            return "Select a successfully probed video background"
        video = self._background.primary_video_stream
        if video is None or video.frame_rate is None:
            return "Selected video has no usable frame rate"
        if not self.playlist.tracks:
            return "Add at least one audio track"
        try:
            parse_target_duration(self.target_duration.text())
        except ValueError as error:
            return str(error)
        output = Path(self.output_path.text()).expanduser()
        if output.suffix.lower() != ".mp4":
            return "Output path must end in .mp4"
        if not output.parent.is_dir():
            return "Output folder does not exist"
        return None

    @Slot()
    def _refresh_render(self) -> None:
        error = self._render_error()
        self.render_button.setEnabled(self._render_request is None and error is None)
        if self._render_request is None and error:
            self.render_status.setText(error)

    @Slot()
    def start_render(self) -> None:
        if self._render_request is not None:
            return
        if (error := self._render_error()) is not None:
            self.render_status.setText(error)
            return
        assert self.render_controller is not None and self._background is not None
        request = UIRenderRequest(
            self._background.path,
            self.playlist,
            parse_target_duration(self.target_duration.text()),
            Path(self.output_path.text()).expanduser(),
        )
        self.render_button.setEnabled(False)
        self.render_status.setText(
            "Rendering… Active render cannot be cancelled; closing hides its result."
        )
        try:
            self._render_request = self.render_controller.render(request)
        except Exception as error:
            self.render_status.setText(str(error))
            self._render_request = None
            self._refresh_render()

    @Slot(str, object)
    def _render_succeeded(self, request_id: str, result: object) -> None:
        if self._closed or request_id != self._render_request:
            return
        self._render_request = None
        path = getattr(result, "path", self.output_path.text())
        self.render_status.setText(f"Rendered: {path}")
        self._refresh_render()

    @Slot(str, str)
    def _render_failed(self, request_id: str, message: str) -> None:
        if self._closed or request_id != self._render_request:
            return
        self._render_request = None
        self.render_status.setText(f"Render failed: {message}")
        self._refresh_render()

    @Slot()
    def refresh_timeline(self) -> None:
        self._refresh_render()
        self.timeline_preview.clear()
        self.copy_timestamps_button.setEnabled(False)
        if not self.playlist.tracks:
            self.timeline_status.setText("Add tracks to build timestamps")
            return
        try:
            target = parse_target_duration(self.target_duration.text())
            plan = self.engine.render_target(self.playlist, target)
        except (ValueError, TrackError) as error:
            self.timeline_status.setText(str(error))
            return
        preview = preview_youtube_chapters(plan.iter_timeline(), 200)
        self.timeline_preview.setPlainText(preview.text)
        starts = [
            format_chapter_timestamp(entry.output_start)
            for entry in islice(plan.iter_timeline(), 200)
        ]
        warnings: list[str] = []
        if preview.truncated:
            warnings.append(f"Preview limited to 200 of {plan.timeline_entry_count} lines")
        if len(starts) != len(set(starts)):
            warnings.append("Warning: duplicate formatted timestamp starts")
        if plan.timeline_entry_count > 10000:
            count = plan.timeline_entry_count
            warnings.append(f"Cannot copy {count} lines; reduce target to 10000 lines or fewer")
        self.timeline_status.setText("; ".join(warnings))
        self.copy_timestamps_button.setEnabled(plan.timeline_entry_count <= 10000)

    @Slot()
    def copy_timestamps(self) -> None:
        try:
            target = parse_target_duration(self.target_duration.text())
            plan = self.engine.render_target(self.playlist, target)
        except (ValueError, TrackError) as error:
            self.timeline_status.setText(str(error))
            return
        if plan.timeline_entry_count > 10000:
            self.timeline_status.setText(
                f"Cannot copy {plan.timeline_entry_count} lines; reduce target below 10000 lines"
            )
            return
        QApplication.clipboard().setText(format_youtube_chapters(plan.iter_timeline()))

    @Slot()
    def browse(self) -> None:
        names, _ = QFileDialog.getOpenFileNames(
            self, "Choose audio or playlist files", str(Path.home()), "Audio and playlists (*)"
        )
        self.ingest_paths([Path(name) for name in names])

    def ingest_paths(self, paths: list[Path] | tuple[Path, ...]) -> None:
        expanded: list[Path] = []
        issues: list[str] = []
        for path in paths:
            if path.suffix.lower() in SUPPORTED_PLAYLIST_SUFFIXES:
                result = import_playlist(path)
                expanded.extend(result.paths)
                issues.extend(issue.message for issue in result.issues)
            else:
                expanded.append(path)
        urls = [QUrl.fromLocalFile(str(path)) for path in expanded]
        for path in extract_local_files(urls):
            index = len(self._pending)
            self._pending.append((path, None))
            self.list.addItem(f"Pending · {path.name}")
            if self.controller:
                self._requests[self.controller.probe(path)] = index
            else:
                self._pending[index] = (path, "Media tools unavailable")
        if issues:
            self.error_label.setText("; ".join(issues))
        self._flush()

    @Slot(str, object)
    def _probe_succeeded(self, request_id: str, info: object) -> None:
        index = self._requests.pop(request_id, None)
        if index is None:
            return
        try:
            track = track_from_media(cast(MediaInfo, info))
        except TrackError as error:
            self._pending[index] = (self._pending[index][0], str(error))
        else:
            self._pending[index] = (self._pending[index][0], track)
        self._flush()

    @Slot(str, str)
    def _probe_failed(self, request_id: str, message: str) -> None:
        index = self._requests.pop(request_id, None)
        if index is not None:
            self._pending[index] = (self._pending[index][0], message)
            self._flush()

    def _flush(self) -> None:
        while self._pending and self._pending[0][1] is not None:
            path, result = self._pending.pop(0)
            self._requests = {request: index - 1 for request, index in self._requests.items()}
            self.list.takeItem(0)
            if isinstance(result, Track):
                self.playlist = self.engine.add(self.playlist, result)
            else:
                self.error_label.setText(f"{path.name}: {result}")
        self.refresh()

    def refresh(self) -> None:
        pending = [self.list.item(index).text() for index in range(self.list.count())]
        self.list.clear()
        for track in self.playlist.tracks:
            self.list.addItem(track.display_title)
        self.list.addItems(pending)
        total = int(self.engine.total_duration(self.playlist) * 1000)
        self.total_label.setText(f"Total: {format_duration(total)}")
        self.refresh_timeline()
        self._refresh_render()

    def _selected(self) -> Track | None:
        row = self.list.currentRow()
        return self.playlist.tracks[row] if 0 <= row < len(self.playlist.tracks) else None

    @Slot(int)
    def select(self, row: int) -> None:
        track = self._selected()
        if track:
            self.player.stop()
            self.player.setSource(QUrl.fromLocalFile(str(track.path)))
            self.play_button.setText("Play")
            self.waveform_label.clear()
            self._waveform_track = track.id
            if self.waveform_controller:
                self._waveform_request = self.waveform_controller.render(
                    WaveformRequest(track.path, track.stream_index, 800, 96)
                )

    @Slot(str, object)
    def _waveform_succeeded(self, request_id: str, output: object) -> None:
        track = self._selected()
        if (
            request_id == self._waveform_request
            and track is not None
            and track.id == self._waveform_track
        ):
            self.waveform_label.setPixmap(QPixmap(str(cast(Path, output))))

    @Slot(str, str)
    def _waveform_failed(self, request_id: str, message: str) -> None:
        if request_id == self._waveform_request:
            self.waveform_label.setText(message)

    @Slot()
    def remove(self) -> None:
        if track := self._selected():
            self.player.stop()
            self.player.setSource(QUrl())
            self.play_button.setText("Play")
            self.playlist = self.engine.remove(self.playlist, track.id)
            self.refresh()

    @Slot()
    def up(self) -> None:
        self._move(-1)

    @Slot()
    def down(self) -> None:
        self._move(1)

    def _move(self, offset: int) -> None:
        row = self.list.currentRow()
        track = self._selected()
        target = row + offset
        if track and 0 <= target < len(self.playlist.tracks):
            self.playlist = self.engine.move(self.playlist, track.id, target)
            self.refresh()
            self.list.setCurrentRow(target)

    @Slot()
    def duplicate(self) -> None:
        if track := self._selected():
            row = self.list.currentRow()
            self.playlist = self.engine.duplicate(self.playlist, track.id)
            self.refresh()
            self.list.setCurrentRow(row + 1)

    @Slot()
    def rename(self) -> None:
        track = self._selected()
        if not track:
            return
        title, accepted = QInputDialog.getText(self, "Rename track", "Title", text=track.title)
        if accepted and title.strip():
            self.playlist = self.engine.rename(self.playlist, track.id, title)
            self.refresh()

    @Slot()
    def toggle_play(self) -> None:
        if not self._selected():
            return
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
            self.play_button.setText("Play")
        else:
            self.player.play()
            self.play_button.setText("Pause")

    @Slot(int)
    def _position_changed(self, value: int) -> None:
        self.seek.setValue(value)
        self.time_label.setText(
            f"{format_duration(value)} / {format_duration(self.player.duration())}"
        )

    @Slot(int)
    def _duration_changed(self, value: int) -> None:
        self.seek.setRange(0, value)
        self._position_changed(self.player.position())

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if extract_local_files(event.mimeData().urls()):
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:
        paths = extract_local_files(event.mimeData().urls())
        if paths:
            self.ingest_paths(paths)
            event.acceptProposedAction()

    def close(self) -> bool:
        self._closed = True
        self.player.stop()
        if self.waveform_controller:
            self.waveform_controller.close()
        if self.render_controller:
            self.render_controller.close()
        return super().close()
