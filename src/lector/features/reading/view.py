import os
from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QLabel,
    QPushButton, QScrollArea, QFrame, QSpinBox,
    QGraphicsDropShadowEffect,
)
from PySide6.QtCore import Signal, Qt, QTimer
from PySide6.QtGui import QImage, QPixmap, QShortcut, QKeySequence, QColor

from lector.shared.theme import LIGHT
from lector.features.reading.document import PdfDocument


def _fitz_to_qpixmap(pix) -> QPixmap:
    img = QImage(pix.samples, pix.width, pix.height, pix.stride,
                 QImage.Format.Format_RGB888)
    return QPixmap.fromImage(img)


class ReadingView(QWidget):
    back_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._doc = PdfDocument()
        self._title = ""
        self._build_ui()
        self._setup_shortcuts()

    def load_pdf(self, path: str):
        self._doc.open(path)
        self._title = os.path.splitext(os.path.basename(path))[0]
        self._refresh()

    # ------------------------------------------------------------------ #
    # UI construction                                                      #
    # ------------------------------------------------------------------ #

    def _build_ui(self):
        t = LIGHT
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_topbar(t))
        root.addWidget(self._hline(t))

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        body.addWidget(self._build_toolbar(t))
        body.addWidget(self._vline(t))
        body.addWidget(self._build_page_area(t), 1)

        body_widget = QWidget()
        body_widget.setLayout(body)
        root.addWidget(body_widget, 1)

    def _build_topbar(self, t) -> QWidget:
        bar = QWidget()
        bar.setFixedHeight(48)
        bar.setStyleSheet(f"background-color: {t['panel_bg']};")

        layout = QHBoxLayout(bar)
        layout.setContentsMargins(12, 0, 16, 0)
        layout.setSpacing(0)

        # Back breadcrumb
        back_btn = QPushButton("← Library")
        back_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: transparent;
                color: {t['text_secondary']};
                border: none;
                padding: 4px 10px;
                font-size: 13px;
            }}
            QPushButton:hover {{ color: {t['text_primary']}; }}
        """)
        back_btn.clicked.connect(self.back_requested)
        layout.addWidget(back_btn)

        layout.addSpacing(8)

        # Document title
        self._title_label = QLabel()
        self._title_label.setStyleSheet(f"""
            color: {t['text_primary']};
            font-size: 14px;
            font-weight: 600;
        """)
        layout.addWidget(self._title_label)

        layout.addStretch()

        # Prev button
        self._prev_btn = self._topbar_nav_btn("‹", t)
        self._prev_btn.setToolTip("Previous page  (←)")
        self._prev_btn.clicked.connect(self._on_prev)
        layout.addWidget(self._prev_btn)

        # Page spinbox
        self._page_spin = QSpinBox()
        self._page_spin.setFixedWidth(54)
        self._page_spin.setFixedHeight(30)
        self._page_spin.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._page_spin.setMinimum(1)
        self._page_spin.setMaximum(1)
        self._page_spin.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        self._page_spin.setStyleSheet(f"""
            QSpinBox {{
                background-color: {t['input_bg']};
                border: 1px solid {t['border_subtle']};
                border-radius: 4px;
                color: {t['text_primary']};
                font-size: 13px;
                padding: 0px 4px;
            }}
        """)
        self._page_spin.valueChanged.connect(self._on_jump)
        layout.addWidget(self._page_spin)

        # Next button
        self._next_btn = self._topbar_nav_btn("›", t)
        self._next_btn.setToolTip("Next page  (→)")
        self._next_btn.clicked.connect(self._on_next)
        layout.addWidget(self._next_btn)

        layout.addSpacing(8)

        # Divider
        div = QLabel("|")
        div.setStyleSheet(f"color: {t['border_light']}; font-size: 16px; padding: 0 4px;")
        layout.addWidget(div)

        layout.addSpacing(4)

        # Zoom label
        self._zoom_label = QLabel("150%")
        self._zoom_label.setFixedWidth(44)
        self._zoom_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._zoom_label.setStyleSheet(f"color: {t['text_muted']}; font-size: 13px;")
        layout.addWidget(self._zoom_label)

        return bar

    def _topbar_nav_btn(self, text: str, t) -> QPushButton:
        btn = QPushButton(text)
        btn.setFixedSize(30, 30)
        btn.setStyleSheet(f"""
            QPushButton {{
                background-color: transparent;
                color: {t['text_secondary']};
                border: none;
                border-radius: 4px;
                font-size: 20px;
                padding: 0px;
            }}
            QPushButton:hover {{
                background-color: {t['surface']};
                color: {t['text_primary']};
            }}
            QPushButton:disabled {{ color: {t['border_light']}; }}
        """)
        return btn

    def _build_toolbar(self, t) -> QWidget:
        bar = QWidget()
        bar.setFixedWidth(52)
        bar.setStyleSheet(f"background-color: {t['panel_bg']};")

        layout = QVBoxLayout(bar)
        layout.setContentsMargins(8, 16, 8, 16)
        layout.setSpacing(6)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        # Tools — only zoom is functional in Milestone 2
        tools = [
            ("✎",  "Highlight text  (Milestone 3)",     False, None),
            ("⌕",  "Search in document  (Milestone 4)", False, None),
        ]
        for char, tip, _, cb in tools:
            layout.addWidget(self._rail_btn(char, tip, t, cb))

        layout.addSpacing(8)
        layout.addWidget(self._rail_separator(t))
        layout.addSpacing(8)

        layout.addWidget(self._rail_btn("+", "Zoom in  (=)", t, self._on_zoom_in))
        layout.addWidget(self._rail_btn("−", "Zoom out  (−)", t, self._on_zoom_out))

        layout.addSpacing(8)
        layout.addWidget(self._rail_separator(t))
        layout.addSpacing(8)

        # Book layout active (visual indicator only — strip comes in Milestone 4)
        book_btn = self._rail_btn("▤", "Book layout  (active)", t, None)
        book_btn.setStyleSheet(book_btn.styleSheet() + f"""
            QPushButton {{ background-color: {t['surface']}; color: {t['accent']}; }}
        """)
        layout.addWidget(book_btn)

        layout.addWidget(self._rail_btn("◑", "Theme  (Milestone 4)", t, None))
        layout.addStretch()
        layout.addWidget(self._rail_btn("?", "What can I say?  (Milestone 8)", t, None))

        return bar

    def _build_page_area(self, t) -> QWidget:
        area = QWidget()
        area.setStyleSheet(f"background-color: {t['canvas_bg']};")

        layout = QVBoxLayout(area)
        layout.setContentsMargins(0, 24, 0, 24)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._scroll = QScrollArea()
        self._scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._scroll.setWidgetResizable(False)
        self._scroll.setStyleSheet(f"background-color: {t['canvas_bg']}; border: none;")

        # Page card with drop shadow
        self._page_card = QWidget()
        self._page_card.setStyleSheet("background-color: white; border-radius: 2px;")
        shadow = QGraphicsDropShadowEffect()
        shadow.setBlurRadius(24)
        shadow.setOffset(0, 4)
        shadow.setColor(QColor(0, 0, 0, 45))
        self._page_card.setGraphicsEffect(shadow)

        card_layout = QVBoxLayout(self._page_card)
        card_layout.setContentsMargins(0, 0, 0, 0)

        self._page_label = QLabel()
        self._page_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        card_layout.addWidget(self._page_label)

        self._scroll.setWidget(self._page_card)
        layout.addWidget(self._scroll, 1)

        return area

    # ------------------------------------------------------------------ #
    # Shortcuts                                                            #
    # ------------------------------------------------------------------ #

    def _setup_shortcuts(self):
        ctx = Qt.ShortcutContext.WidgetWithChildrenShortcut
        for key in (Qt.Key.Key_Right, Qt.Key.Key_PageDown, Qt.Key.Key_Down):
            QShortcut(QKeySequence(key), self, self._on_next, context=ctx)
        for key in (Qt.Key.Key_Left, Qt.Key.Key_PageUp, Qt.Key.Key_Up):
            QShortcut(QKeySequence(key), self, self._on_prev, context=ctx)
        QShortcut(QKeySequence("="), self, self._on_zoom_in, context=ctx)
        QShortcut(QKeySequence("-"), self, self._on_zoom_out, context=ctx)

    # ------------------------------------------------------------------ #
    # Actions                                                              #
    # ------------------------------------------------------------------ #

    def _on_prev(self):
        if self._doc.prev_page():
            self._refresh()

    def _on_next(self):
        if self._doc.next_page():
            self._refresh()

    def _on_jump(self, value: int):
        if self._doc.go_to_page(value - 1):
            self._refresh()

    def _on_zoom_in(self):
        self._doc.zoom_in()
        self._refresh()

    def _on_zoom_out(self):
        self._doc.zoom_out()
        self._refresh()

    # ------------------------------------------------------------------ #
    # Render                                                               #
    # ------------------------------------------------------------------ #

    def _refresh(self):
        if not self._doc.is_open:
            return

        pix = self._doc.render_current_page()
        if pix:
            qt_pix = _fitz_to_qpixmap(pix)
            self._page_label.setPixmap(qt_pix)
            self._page_label.resize(qt_pix.size())
            self._page_card.resize(qt_pix.size())

        self._title_label.setText(self._title)

        self._page_spin.blockSignals(True)
        self._page_spin.setMaximum(self._doc.page_count)
        self._page_spin.setValue(self._doc.page_index + 1)
        self._page_spin.blockSignals(False)

        self._zoom_label.setText(f"{int(self._doc.zoom * 100)}%")
        self._prev_btn.setEnabled(self._doc.page_index > 0)
        self._next_btn.setEnabled(self._doc.page_index < self._doc.page_count - 1)

    # ------------------------------------------------------------------ #
    # Helpers                                                              #
    # ------------------------------------------------------------------ #

    def _rail_btn(self, text: str, tooltip: str, t, callback) -> QPushButton:
        btn = QPushButton(text)
        btn.setToolTip(tooltip)
        btn.setFixedSize(36, 36)
        btn.setStyleSheet(f"""
            QPushButton {{
                background-color: transparent;
                color: {t['text_secondary']};
                border: none;
                border-radius: 6px;
                font-size: 16px;
                padding: 0px;
            }}
            QPushButton:hover {{
                background-color: {t['surface']};
                color: {t['text_primary']};
            }}
            QPushButton:pressed {{ background-color: {t['border_light']}; }}
        """)
        if callback:
            btn.clicked.connect(callback)
        else:
            btn.setEnabled(False)
            btn.setStyleSheet(btn.styleSheet() + f"""
                QPushButton:disabled {{ color: {t['border_muted']}; }}
            """)
        return btn

    @staticmethod
    def _rail_separator(t) -> QFrame:
        f = QFrame()
        f.setFrameShape(QFrame.Shape.HLine)
        f.setFixedHeight(1)
        f.setStyleSheet(f"background-color: {t['border_subtle']}; border: none;")
        return f

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
        f.setStyleSheet(f"background-color: {t['border_light']}; border: none;")
        return f
