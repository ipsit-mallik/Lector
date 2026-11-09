"""What the reader can choose in the tool dialogs, and the defaults. Pure, so
settings (`settings/store.py`), the bridge and the engine share one list."""

LIGHT = "light"
BALANCED = "balanced"
STRONG = "strong"
LEVELS = (LIGHT, BALANCED, STRONG)
DEFAULT_LEVEL = BALANCED

PNG = "png"
JPEG = "jpeg"
EXPORT_FORMATS = (PNG, JPEG)
DEFAULT_FORMAT = PNG

EXPORT_PPI = (96, 150, 300)
DEFAULT_PPI = 150
