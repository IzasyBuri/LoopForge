from PySide6.QtWidgets import QLabel, QMainWindow

from .metadata import APP_NAME, VERSION


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} {VERSION}")
        self.setMinimumSize(800, 500)
        self.setCentralWidget(QLabel(APP_NAME))
