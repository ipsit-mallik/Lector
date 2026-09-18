import sys
from PySide6.QtWidgets import QApplication

from lector.shared.theme import LIGHT, build_stylesheet
from lector.app import MainWindow


def main():
    app = QApplication(sys.argv)
    app.setStyleSheet(build_stylesheet(LIGHT))
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
