from __future__ import annotations

import sys
from importlib.resources import files
from pathlib import Path

from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication

from .lifecycle import Lifecycle
from .metadata import APP_NAME, ORGANIZATION_DOMAIN, ORGANIZATION_NAME, VERSION
from .window import MainWindow


def create_application(arguments: list[str]) -> tuple[QApplication, Lifecycle, MainWindow]:
    QCoreApplication.setApplicationName(APP_NAME)
    QCoreApplication.setApplicationVersion(VERSION)
    QCoreApplication.setOrganizationName(ORGANIZATION_NAME)
    QCoreApplication.setOrganizationDomain(ORGANIZATION_DOMAIN)
    app = QApplication(arguments)
    resources = files("loopforge")
    app.setStyleSheet(resources.joinpath("dark.qss").read_text(encoding="utf-8"))
    lifecycle = Lifecycle(bundled_dir=Path(str(resources.joinpath("bin"))))
    runtime = lifecycle.startup()
    app.aboutToQuit.connect(lifecycle.shutdown)
    window = MainWindow(runtime)
    return app, lifecycle, window


def main() -> int:
    app, _, window = create_application(sys.argv)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
