from __future__ import annotations

from decimal import Decimal
from fractions import Fraction
from pathlib import Path

from PySide6.QtCore import Qt, QUrl, Signal, Slot
from PySide6.QtGui import QCloseEvent, QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMainWindow,
    QPushButton,
    QScrollArea,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from .lifecycle import Runtime
from .metadata import APP_NAME, VERSION
from .models import MediaInfo
from .probe_tasks import ProbeController, extract_local_files


def _value(value: object | None, suffix: str = "") -> str:
    return f"{value}{suffix}" if value is not None else "Unknown"


def _duration(value: Decimal | None) -> str:
    if value is None:
        return "Unknown"
    seconds = float(value)
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{int(hours):02}:{int(minutes):02}:{seconds:05.2f}"


def _fps(value: Fraction | None) -> str:
    return "Unknown" if value is None else f"{float(value):.3f} fps ({value})"


class DropTarget(QFrame):
    files_dropped = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("dropTarget")
        self.setAcceptDrops(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName("Media file drop target")
        layout = QVBoxLayout(self)
        title = QLabel("Drop video or audio files here")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint = QLabel("Local files only · Multiple files supported")
        hint.setObjectName("muted")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)
        layout.addWidget(hint)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if extract_local_files(event.mimeData().urls()):
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:
        paths = extract_local_files(event.mimeData().urls())
        if paths:
            self.files_dropped.emit(paths)
            event.acceptProposedAction()


class MediaCard(QFrame):
    selected = Signal(object)

    def __init__(self, path: Path) -> None:
        super().__init__()
        self.path = path
        self.info: MediaInfo | None = None
        self.setObjectName("mediaCard")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName(f"Media item {path.name}")
        layout = QVBoxLayout(self)
        self.name_label = QLabel(path.name)
        self.name_label.setObjectName("cardTitle")
        self.name_label.setWordWrap(True)
        self.state_label = QLabel("Pending · Reading media details…")
        self.state_label.setObjectName("pendingState")
        self.detail_label = QLabel(str(path))
        self.detail_label.setObjectName("muted")
        self.detail_label.setWordWrap(True)
        layout.addWidget(self.name_label)
        layout.addWidget(self.state_label)
        layout.addWidget(self.detail_label)

    def mousePressEvent(self, event: object) -> None:
        self.selected.emit(self)

    def set_ready(self, info: MediaInfo) -> None:
        self.info = info
        self.state_label.setObjectName("readyState")
        self.state_label.setText("Ready")
        video = info.primary_video_stream
        audio = info.primary_audio_stream
        if video:
            summary = (
                f"Video · {_value(video.width)} × {_value(video.height)} · "
                f"{_value(video.codec_name)} · {_fps(video.frame_rate)}"
            )
        elif audio:
            summary = (
                f"Audio · {_value(audio.codec_name)} · "
                f"{_value(audio.sample_rate, ' Hz')} · {_value(audio.channels, ' channels')}"
            )
        else:
            summary = "Media"
        self.detail_label.setText(f"{summary}\nDuration: {_duration(info.duration)}")
        self.style().unpolish(self)
        self.style().polish(self)

    def set_error(self, message: str) -> None:
        self.state_label.setObjectName("errorState")
        self.state_label.setText("Error · Could not read media")
        self.detail_label.setText(message)
        self.style().unpolish(self)
        self.style().polish(self)


class MainWindow(QMainWindow):
    def __init__(self, runtime: Runtime) -> None:
        super().__init__()
        self.runtime = runtime
        self.controller = (
            ProbeController(runtime.media_probe, self) if runtime.media_probe else None
        )
        self._requests: dict[str, MediaCard] = {}
        self._cards: list[MediaCard] = []
        self.setWindowTitle(f"{APP_NAME} {VERSION}")
        self.setMinimumSize(960, 620)
        self.setAcceptDrops(True)
        self._build_shell()
        if self.controller:
            self.controller.succeeded.connect(self._probe_succeeded)
            self.controller.failed.connect(self._probe_failed)
        else:
            self.tool_status.setText(
                "Media tools unavailable · Install ffmpeg and ffprobe to inspect files"
            )

    def _build_shell(self) -> None:
        root = QWidget()
        outer = QHBoxLayout(root)
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        side = QVBoxLayout(sidebar)
        brand = QLabel(APP_NAME)
        brand.setObjectName("brand")
        side.addWidget(brand)
        nav = QListWidget()
        nav.setAccessibleName("Workspace navigation")
        nav.addItem("Media")
        nav.setCurrentRow(0)
        side.addWidget(nav)
        side.addStretch()
        self.tool_status = QLabel("Media tools ready")
        self.tool_status.setObjectName("muted")
        self.tool_status.setWordWrap(True)
        side.addWidget(self.tool_status)
        outer.addWidget(sidebar)
        splitter = QSplitter()
        media_pane = QWidget()
        media_layout = QVBoxLayout(media_pane)
        heading = QLabel("Media")
        heading.setObjectName("heading")
        media_layout.addWidget(heading)
        self.drop_target = DropTarget()
        self.drop_target.files_dropped.connect(self.ingest_paths)
        media_layout.addWidget(self.drop_target)
        browse = QPushButton("Browse files…")
        browse.setAccessibleName("Browse for media files")
        browse.clicked.connect(self.browse)
        media_layout.addWidget(browse)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setAccessibleName("Imported media")
        cards_widget = QWidget()
        self.cards_layout = QVBoxLayout(cards_widget)
        self.cards_layout.addStretch()
        scroll.setWidget(cards_widget)
        media_layout.addWidget(scroll, 1)
        preview = QFrame()
        preview.setObjectName("previewPane")
        preview_layout = QVBoxLayout(preview)
        preview_title = QLabel("Preview")
        preview_title.setObjectName("heading")
        self.preview_placeholder = QLabel(
            "Select media to view details\nPlayback arrives in a later phase"
        )
        self.preview_placeholder.setObjectName("previewPlaceholder")
        self.preview_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_placeholder.setWordWrap(True)
        self.detail_display = QLabel("No media selected")
        self.detail_display.setObjectName("detailDisplay")
        self.detail_display.setWordWrap(True)
        self.detail_display.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByKeyboard)
        preview_layout.addWidget(preview_title)
        preview_layout.addWidget(self.preview_placeholder, 1)
        preview_layout.addWidget(self.detail_display)
        splitter.addWidget(media_pane)
        splitter.addWidget(preview)
        splitter.setSizes([400, 600])
        outer.addWidget(splitter, 1)
        self.setCentralWidget(root)

    @Slot()
    def browse(self) -> None:
        names, _ = QFileDialog.getOpenFileNames(
            self, "Choose media files", str(Path.home()), "All files (*)"
        )
        self.ingest_paths(tuple(Path(name) for name in names))

    @Slot(object)
    def ingest_paths(self, paths: tuple[Path, ...] | list[Path]) -> None:
        urls = [QUrl.fromLocalFile(str(path)) for path in paths]
        for path in extract_local_files(urls):
            card = MediaCard(path)
            card.selected.connect(self.show_card)
            self._cards.append(card)
            self.cards_layout.insertWidget(self.cards_layout.count() - 1, card)
            if self.controller:
                request_id = self.controller.probe(path)
                self._requests[request_id] = card
            else:
                card.set_error(
                    "ffmpeg and ffprobe are unavailable. Configure or install media tools, "
                    "then restart LoopForge."
                )

    @Slot(str, object)
    def _probe_succeeded(self, request_id: str, info: MediaInfo) -> None:
        card = self._requests.pop(request_id, None)
        if card:
            card.set_ready(info)
            self.show_card(card)

    @Slot(str, str)
    def _probe_failed(self, request_id: str, message: str) -> None:
        card = self._requests.pop(request_id, None)
        if card:
            card.set_error(message)
            self.show_card(card)

    @Slot(object)
    def show_card(self, card: MediaCard) -> None:
        self.detail_display.setText(
            f"{card.path}\n\n{card.state_label.text()}\n{card.detail_label.text()}"
        )

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if extract_local_files(event.mimeData().urls()):
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:
        paths = extract_local_files(event.mimeData().urls())
        if paths:
            self.ingest_paths(paths)
            event.acceptProposedAction()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.controller:
            self.controller.close()
        super().closeEvent(event)
