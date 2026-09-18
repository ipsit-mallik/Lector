from PySide6.QtWidgets import QMainWindow, QStackedWidget

from lector.shared.theme import LIGHT, build_stylesheet
from lector.features.home.view import HomeView
from lector.features.reading.view import ReadingView


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Lector")
        self.resize(1280, 820)

        self._stack = QStackedWidget()
        self.setCentralWidget(self._stack)

        self._home = HomeView()
        self._reading = ReadingView()
        self._stack.addWidget(self._home)
        self._stack.addWidget(self._reading)

        self._home.pdf_opened.connect(self._open_pdf)
        self._reading.back_requested.connect(self._show_home)

    def _open_pdf(self, path: str):
        self._reading.load_pdf(path)
        self._stack.setCurrentWidget(self._reading)

    def _show_home(self):
        self._stack.setCurrentWidget(self._home)
