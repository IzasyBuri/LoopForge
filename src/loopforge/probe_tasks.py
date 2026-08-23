from __future__ import annotations

import logging
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QUrl, Signal, Slot

from .models import MediaInfo

logger = logging.getLogger(__name__)


class ProbeService(Protocol):
    def probe(self, path: Path | str, timeout: float | None = 30) -> MediaInfo: ...


def extract_local_files(urls: list[QUrl]) -> tuple[Path, ...]:
    paths: list[Path] = []
    seen: set[Path] = set()
    for url in urls:
        if not url.isLocalFile():
            continue
        path = Path(url.toLocalFile())
        if not path.is_file():
            continue
        resolved = path.resolve()
        key = Path(str(resolved).casefold())
        if key not in seen:
            seen.add(key)
            paths.append(resolved)
    return tuple(paths)


class _ProbeSignals(QObject):
    succeeded = Signal(str, object)
    failed = Signal(str, str)


class _ProbeTask(QRunnable):
    def __init__(self, request_id: str, path: Path, service: ProbeService) -> None:
        super().__init__()
        self.request_id = request_id
        self.path = path
        self.service = service
        self.signals = _ProbeSignals()

    @Slot()
    def run(self) -> None:
        try:
            info = self.service.probe(self.path)
        except Exception as error:
            logger.exception("Media probe task failed for %s", self.path)
            self.signals.failed.emit(self.request_id, str(error))
        else:
            self.signals.succeeded.emit(self.request_id, info)


class ProbeController(QObject):
    succeeded = Signal(str, object)
    failed = Signal(str, str)

    def __init__(self, service: ProbeService, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._service = service
        self._pool = QThreadPool(self)
        self._active: set[str] = set()
        self._closed = False

    def probe(self, path: Path) -> str:
        if self._closed:
            raise RuntimeError("Probe controller is closed")
        request_id = uuid4().hex
        task = _ProbeTask(request_id, path, self._service)
        self._active.add(request_id)
        task.signals.succeeded.connect(self._succeeded)
        task.signals.failed.connect(self._failed)
        self._pool.start(task)
        return request_id

    @Slot(str, object)
    def _succeeded(self, request_id: str, info: MediaInfo) -> None:
        if self._closed or request_id not in self._active:
            return
        self._active.remove(request_id)
        self.succeeded.emit(request_id, info)

    @Slot(str, str)
    def _failed(self, request_id: str, message: str) -> None:
        if self._closed or request_id not in self._active:
            return
        self._active.remove(request_id)
        self.failed.emit(request_id, message)

    def cancel(self, request_id: str) -> bool:
        if request_id not in self._active:
            return False
        self._active.remove(request_id)
        return True

    def close(self) -> None:
        self._closed = True
        self._active.clear()
        self._pool.clear()
