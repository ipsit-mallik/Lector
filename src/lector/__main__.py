import sys
from PySide6.QtWidgets import QApplication, QMainWindow


def main():
    app = QApplication(sys.argv)
    window = QMainWindow()
    window.setWindowTitle("Lector")
    window.resize(1200, 800)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
