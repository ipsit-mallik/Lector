from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QLabel,
    QPushButton, QFileDialog, QFrame, QLineEdit,
)
from PySide6.QtCore import Signal, Qt

from lector.shared.theme import LIGHT


class HomeView(QWidget):
    pdf_opened = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self):
        t = LIGHT
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_sidebar(t))
        root.addWidget(self._vline(t))
        root.addWidget(self._build_content(t), 1)

    # ------------------------------------------------------------------ #
    # Sidebar                                                              #
    # ------------------------------------------------------------------ #

    def _build_sidebar(self, t) -> QWidget:
        sidebar = QWidget()
        sidebar.setFixedWidth(220)
        sidebar.setStyleSheet(f"background-color: {t['panel_bg']};")

        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(12, 20, 12, 20)
        layout.setSpacing(2)

        # Brand
        brand = QWidget()
        brand_row = QHBoxLayout(brand)
        brand_row.setContentsMargins(8, 0, 8, 16)
        brand_row.setSpacing(8)
        icon = QLabel("◎")
        icon.setStyleSheet(f"color: {t['accent']}; font-size: 18px;")
        name = QLabel("Lector")
        name.setStyleSheet(f"color: {t['text_primary']}; font-size: 16px; font-weight: 700;")
        brand_row.addWidget(icon)
        brand_row.addWidget(name)
        brand_row.addStretch()
        layout.addWidget(brand)

        nav_items = [
            ("○", "Recent",    True),
            ("□", "All PDFs",  False),
            ("★", "Favorites", False),
            ("◇", "Settings",  False),
        ]
        for icon_char, label, active in nav_items:
            btn = self._nav_btn(icon_char, label, active, t)
            layout.addWidget(btn)

        layout.addStretch()

        # Voice status placeholder (wired in Milestone 5)
        voice_box = QWidget()
        voice_box.setStyleSheet(f"""
            background-color: {t['surface']};
            border-radius: 8px;
            border: 1px solid {t['border_subtle']};
        """)
        vb_layout = QVBoxLayout(voice_box)
        vb_layout.setContentsMargins(12, 10, 12, 10)
        vb_layout.setSpacing(2)
        status_row = QHBoxLayout()
        dot = QLabel("●")
        dot.setStyleSheet(f"color: {t['border_muted']}; font-size: 10px;")
        status_lbl = QLabel("VOICE READY")
        status_lbl.setStyleSheet(f"color: {t['text_very_muted']}; font-size: 10px; font-weight: 700; letter-spacing: 1px;")
        status_row.addWidget(dot)
        status_row.addWidget(status_lbl)
        status_row.addStretch()
        vb_layout.addLayout(status_row)
        hint = QLabel("Hold Space and say what you want.\nOffline — nothing leaves this machine.")
        hint.setStyleSheet(f"color: {t['text_very_muted']}; font-size: 11px;")
        hint.setWordWrap(True)
        vb_layout.addWidget(hint)
        layout.addWidget(voice_box)

        return sidebar

    def _nav_btn(self, icon: str, label: str, active: bool, t) -> QPushButton:
        btn = QPushButton(f"  {icon}  {label}")
        bg     = t["surface"] if active else "transparent"
        color  = t["text_primary"] if active else t["text_secondary"]
        weight = "600" if active else "400"
        btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {bg};
                color: {color};
                border: none;
                border-radius: 6px;
                padding: 9px 12px;
                text-align: left;
                font-weight: {weight};
                font-size: 14px;
            }}
            QPushButton:hover {{
                background-color: {t['surface']};
                color: {t['text_primary']};
            }}
        """)
        return btn

    # ------------------------------------------------------------------ #
    # Main content                                                         #
    # ------------------------------------------------------------------ #

    def _build_content(self, t) -> QWidget:
        content = QWidget()
        content.setStyleSheet(f"background-color: {t['surface']};")

        layout = QVBoxLayout(content)
        layout.setContentsMargins(32, 24, 32, 32)
        layout.setSpacing(0)

        # Top row: search bar + Open PDF button
        top_row = QHBoxLayout()
        top_row.setSpacing(12)

        search = QLineEdit()
        search.setPlaceholderText("Search your library…")
        search.setFixedHeight(36)
        search.setStyleSheet(f"""
            QLineEdit {{
                background-color: {t['input_bg']};
                border: 1px solid {t['border_subtle']};
                border-radius: 6px;
                padding: 0px 12px;
                color: {t['text_primary']};
                font-size: 14px;
            }}
        """)
        top_row.addWidget(search, 1)

        open_btn = QPushButton("⊕  Open PDF")
        open_btn.setFixedHeight(36)
        open_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {t['accent']};
                color: white;
                border: none;
                border-radius: 6px;
                padding: 0px 20px;
                font-weight: 600;
                font-size: 14px;
            }}
            QPushButton:hover {{ background-color: {t['accent_hover']}; }}
            QPushButton:pressed {{ background-color: {t['accent_dark']}; }}
        """)
        open_btn.clicked.connect(self._on_open)
        top_row.addWidget(open_btn)
        layout.addLayout(top_row)

        layout.addSpacing(28)

        # Recent section label
        recent_row = QHBoxLayout()
        recent_lbl = QLabel("RECENT")
        recent_lbl.setStyleSheet(f"""
            color: {t['text_very_muted']};
            font-size: 11px;
            font-weight: 700;
            letter-spacing: 1px;
        """)
        count_lbl = QLabel("last 10 files")
        count_lbl.setStyleSheet(f"color: {t['text_very_muted']}; font-size: 11px;")
        recent_row.addWidget(recent_lbl)
        recent_row.addSpacing(8)
        recent_row.addWidget(count_lbl)
        recent_row.addStretch()
        layout.addLayout(recent_row)

        layout.addSpacing(10)
        layout.addWidget(self._hline(t))
        layout.addSpacing(10)

        # Empty state
        empty = QLabel("No recent files — open a PDF to get started.")
        empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty.setStyleSheet(f"color: {t['text_very_muted']}; font-size: 14px;")
        layout.addWidget(empty, 1)

        return content

    # ------------------------------------------------------------------ #
    # Actions                                                              #
    # ------------------------------------------------------------------ #

    def _on_open(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open PDF", "", "PDF Files (*.pdf)"
        )
        if path:
            self.pdf_opened.emit(path)

    # ------------------------------------------------------------------ #
    # Helpers                                                              #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _vline(t) -> QFrame:
        f = QFrame()
        f.setFrameShape(QFrame.Shape.VLine)
        f.setFixedWidth(1)
        f.setStyleSheet(f"background-color: {t['border_light']}; border: none;")
        return f

    @staticmethod
    def _hline(t) -> QFrame:
        f = QFrame()
        f.setFrameShape(QFrame.Shape.HLine)
        f.setFixedHeight(1)
        f.setStyleSheet(f"background-color: {t['border_subtle']}; border: none;")
        return f
