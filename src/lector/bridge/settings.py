"""Settings as the Settings page and the reader's preferences read and
write them, and the first-run flag (docs/ARCHITECTURE.md § "Bridge layout")."""
from lector.features.annotations import highlighter
from lector.features.onboarding import state as onboarding
from lector.features.settings import store as settings
from lector.shared import window_chrome


class SettingsController:
    def get_save_behavior(self) -> str:
        return settings.get_save_behavior()

    def set_save_behavior(self, mode: str) -> None:
        settings.set_save_behavior(mode)

    def get_reopen_behavior(self) -> str:
        return settings.get_reopen_behavior()

    def set_reopen_behavior(self, mode: str) -> None:
        settings.set_reopen_behavior(mode)

    def get_theme(self) -> str:
        return settings.get_theme()

    def set_theme(self, name: str) -> None:
        settings.set_theme(name)
        window_chrome.apply(window_chrome.WINDOW_TITLE, settings.get_theme(), icon=False)

    def get_recent_view(self) -> str:
        return settings.get_recent_view()

    def set_recent_view(self, mode: str) -> None:
        settings.set_recent_view(mode)

    def get_reading_preferences(self) -> dict:
        """Settings > Reading, plus whether the outline panel was left open
        and how wide: `{"default_view", "progress_bar", "outline_open",
        "outline_width", "outline_width_min", "outline_width_max",
        "highlight_color", "highlight_palette"}`."""
        return {
            "default_view": settings.get_default_view(),
            "progress_bar": settings.get_progress_bar(),
            "outline_open": settings.get_outline_open(),
            "outline_width": settings.get_outline_width(),
            "outline_width_min": settings.OUTLINE_WIDTH_MIN,
            "outline_width_max": settings.OUTLINE_WIDTH_MAX,
            "highlight_color": settings.get_highlight_color(),
            "highlight_palette": list(highlighter.COLOR_NAMES),
        }

    def set_default_view(self, mode: str) -> dict:
        """Ignores anything but book or strip (a bridge boundary) and returns
        the preferences as stored."""
        if mode in (settings.BOOK, settings.STRIP):
            settings.set_default_view(mode)
        return self.get_reading_preferences()

    def set_progress_bar(self, on: bool) -> dict:
        settings.set_progress_bar(bool(on))
        return self.get_reading_preferences()

    def set_outline_open(self, is_open: bool) -> dict:
        settings.set_outline_open(bool(is_open))
        return self.get_reading_preferences()

    def set_outline_width(self, width: float) -> dict:
        """The outline panel's width after a drag. Anything that isn't a
        number is ignored (a bridge boundary); a number is kept within the
        panel's limits."""
        if isinstance(width, (int, float)) and not isinstance(width, bool) and width == width:
            settings.set_outline_width(width)
        return self.get_reading_preferences()

    def get_onboarding_seen(self) -> bool:
        return onboarding.has_seen()

    def set_onboarding_seen(self) -> None:
        onboarding.mark_seen()
