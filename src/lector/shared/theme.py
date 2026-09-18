LIGHT = {
    "canvas_bg":      "#E7E2D9",
    "surface":        "#F6F3EE",
    "panel_bg":       "#EFEAE2",
    "panel_tertiary": "#F2EEE7",
    "input_bg":       "#FFFDF9",
    "text_primary":   "#161A18",
    "text_body":      "#1C211E",
    "text_secondary": "#4A4F49",
    "text_dialog":    "#5A5F58",
    "text_muted":     "#6B6F6A",
    "text_very_muted":"#9A968E",
    "accent":         "#3E6259",
    "accent_hover":   "#33534B",
    "accent_dark":    "#2C463F",
    "voice_focus":    "#C97A3D",
    "highlight":      "#F7DE7A",
    "destructive":    "#C0453B",
    "border_light":   "#D2CCC2",
    "border_subtle":  "#D8D3C9",
    "border_muted":   "#A8A6A0",
}

ACTIVE = LIGHT


def build_stylesheet(tokens: dict) -> str:
    t = tokens
    return f"""
        QWidget {{
            background-color: {t['canvas_bg']};
            color: {t['text_primary']};
            font-family: "Segoe UI", system-ui, "Helvetica Neue", Helvetica, sans-serif;
            font-size: 14px;
        }}
        QPushButton {{
            background-color: {t['accent']};
            color: white;
            border: none;
            border-radius: 6px;
            padding: 8px 18px;
            font-weight: 600;
        }}
        QPushButton:hover {{
            background-color: {t['accent_hover']};
        }}
        QPushButton:pressed {{
            background-color: {t['accent_dark']};
        }}
        QPushButton:disabled {{
            background-color: {t['border_light']};
            color: {t['text_very_muted']};
        }}
        QPushButton#secondary {{
            background-color: {t['surface']};
            color: {t['text_primary']};
            border: 1px solid {t['border_light']};
            font-weight: 500;
        }}
        QPushButton#secondary:hover {{
            background-color: {t['panel_bg']};
        }}
        QPushButton#secondary:disabled {{
            color: {t['text_very_muted']};
            border-color: {t['border_subtle']};
        }}
        QLineEdit {{
            background-color: {t['input_bg']};
            border: 1px solid {t['border_subtle']};
            border-radius: 6px;
            padding: 6px 10px;
            color: {t['text_primary']};
        }}
        QSpinBox {{
            background-color: {t['input_bg']};
            border: 1px solid {t['border_subtle']};
            border-radius: 4px;
            padding: 4px 6px;
            color: {t['text_primary']};
        }}
        QScrollArea {{
            border: none;
            background-color: {t['canvas_bg']};
        }}
        QScrollBar:vertical {{
            background: {t['panel_bg']};
            width: 8px;
            margin: 0px;
        }}
        QScrollBar::handle:vertical {{
            background: {t['border_muted']};
            border-radius: 4px;
            min-height: 24px;
        }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
            height: 0px;
        }}
        QScrollBar:horizontal {{
            background: {t['panel_bg']};
            height: 8px;
            margin: 0px;
        }}
        QScrollBar::handle:horizontal {{
            background: {t['border_muted']};
            border-radius: 4px;
            min-width: 24px;
        }}
        QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
            width: 0px;
        }}
        QToolTip {{
            background-color: {t['text_primary']};
            color: {t['surface']};
            border: none;
            border-radius: 4px;
            padding: 4px 8px;
            font-size: 12px;
        }}
    """
