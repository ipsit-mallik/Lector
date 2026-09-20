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

# Dark and Sepia: docs/DESIGN_SYSTEM.md gives only 8 rough tokens for these
# (Background/Text/Muted text/Accent/Border/Surface/Highlight/Focus
# indicator) and explicitly calls them provisional/unverified — "a starting
# point to confirm, not as finalized as light". The 8 values below are taken
# directly from that table; every other key here is this implementation's
# own interpolation to fill out the full token set LIGHT defines, so the
# same readability/focus-vs-highlight-contrast check the doc calls for still
# needs to happen against these before they're treated as settled.
DARK = {
    "canvas_bg":      "#1C1A17",
    "surface":        "#26231F",
    "panel_bg":       "#211E1A",
    "panel_tertiary": "#232019",
    "input_bg":       "#2C2822",
    "text_primary":   "#EDE8DF",
    "text_body":      "#E5E0D6",
    "text_secondary": "#B7B1A4",
    "text_dialog":    "#C7C1B4",
    "text_muted":     "#9C948A",
    "text_very_muted":"#746D63",
    "accent":         "#6FA394",
    "accent_hover":   "#7FB3A4",
    "accent_dark":    "#5C8B7D",
    "voice_focus":    "#E0954F",
    "highlight":      "#6B5A24",
    "destructive":    "#D9695D",
    "border_light":   "#3A362F",
    "border_subtle":  "#332F29",
    "border_muted":   "#4A453C",
}

SEPIA = {
    "canvas_bg":      "#F1E7D0",
    "surface":        "#F8F0DE",
    "panel_bg":       "#EDE2C8",
    "panel_tertiary": "#F3E9D2",
    "input_bg":       "#FCF7EA",
    "text_primary":   "#3B2F20",
    "text_body":      "#3B2F20",
    "text_secondary": "#5B4C38",
    "text_dialog":    "#5B4C38",
    "text_muted":     "#8A7859",
    "text_very_muted":"#A4967C",
    "accent":         "#4F6B5C",
    "accent_hover":   "#43594D",
    "accent_dark":    "#37483E",
    "voice_focus":    "#B5672E",
    "highlight":      "#E8C468",
    "destructive":    "#B5453A",
    "border_light":   "#DDCBA0",
    "border_subtle":  "#E3D3AC",
    "border_muted":   "#C9B78E",
}

THEMES = {"light": LIGHT, "dark": DARK, "sepia": SEPIA}
THEME_ORDER = ["light", "dark", "sepia"]

ACTIVE = LIGHT


def get_tokens(name: str) -> dict:
    return THEMES.get(name, LIGHT)
