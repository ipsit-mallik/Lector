"""The bridge object exposed to the frontend as `window.pywebview.api`.

Per docs/ARCHITECTURE.md, this is the only file both sides import against:
frontend JS never reaches into `features/` directly, and Python features
never touch `frontend/` files. Every method here is called from
`frontend/js/bridge.js` (or a page's own JS) and returns JSON-serializable
data — no PySide6/Qt types, no live PyMuPDF objects.

`Api` is a facade (docs/ARCHITECTURE.md § "Bridge layout"): each method
hands the call to the controller in `lector/bridge/` that does the work. A
new bridge method is a one-line delegator here, its body in the controller
it belongs to, and its name in `tests/test_bridge_surface.py`.
"""
import operator
from pathlib import Path

import webview  # noqa: F401  (re-exported)

from lector.bridge.checks import MAX_THUMBNAIL_PX, MIN_THUMBNAIL_PX  # noqa: F401  (re-exported)
from lector.bridge.compress import CompressController
from lector.bridge.core import BridgeCore
from lector.bridge.core import log  # noqa: F401  (re-exported: the "lector.api" logger)
from lector.bridge.dialogs import DialogsController
from lector.bridge.file_tool_jobs import FileToolJobsController
from lector.bridge.file_tool_setup import FileToolSetupController
from lector.bridge.highlights import MAX_INK_POINTS  # noqa: F401  (re-exported)
from lector.bridge.highlights import HighlightsController
from lector.bridge.home import HomeController
from lector.bridge.organise import PAGE_CHANGES_FAILED  # noqa: F401  (re-exported)
from lector.bridge.organise import OrganiseController
from lector.bridge.reader import ReaderController
from lector.bridge.save import SaveController
from lector.bridge.settings import SettingsController
from lector.bridge.voice import MICROPHONE_UNAVAILABLE  # noqa: F401  (re-exported)
from lector.bridge.voice import VoiceController
from lector.bridge.window import WindowController
from lector.features.dialogs import browser as dialog_browser
from lector.features.file_tools import compress_plan
from lector.features.home import recent as home_recent  # noqa: F401  (re-exported)
from lector.features.reading import ocr
from lector.features.settings import store as settings
from lector.features.voice import router  # noqa: F401  (re-exported)

# The names marked "re-exported" are reached through this module by the
# tests, which patch some of them here (`webview.windows`, `router.resolve`,
# `home_recent.reveal_in_file_manager`). Each patch changes the one shared
# module object, so it reaches the controller that uses it.


def _shared(owner: str, name: str) -> property:
    """A property reading and writing `name` on the object at `owner`
    (a dotted path from the instance), so code and tests that use the old
    attribute name reach the one shared value."""
    get_owner = operator.attrgetter(owner)

    def get(self):
        return getattr(get_owner(self), name)

    def set_(self, value) -> None:
        setattr(get_owner(self), name, value)

    return property(get, set_)


class Api:
    def __init__(self, log_dir: Path | None = None):
        """`log_dir` is the app's log folder (`shared/app_log.py`); the OCR
        worker writes its crash log there. None, as in tests, writes none."""
        # The document, the services and the voice scope every controller
        # shares (bridge/core.py). The three callbacks forward to controllers
        # built just below: nothing reports before then, since the voice
        # engine only speaks once listening (started at the end of this
        # method) and the workers only once given a job. Keep it that way.
        self._core = BridgeCore(
            log_dir,
            on_voice_result=self._on_voice_result,
            on_ocr_finished=self._on_ocr_finished,
            on_file_job_event=lambda event: self._file_jobs.on_file_job_event(event),
        )
        self._settings = SettingsController()
        self._dialogs = DialogsController()
        self._save = SaveController(self._core)
        self._reader = ReaderController(self._core)
        self._highlights = HighlightsController(self._core)
        self._organise_controller = OrganiseController(self._core)
        self._voice_controller = VoiceController(self._core)
        self._tool_setup = FileToolSetupController(self._core)
        self._compress = CompressController(self._core, self._tool_setup)
        self._file_jobs = FileToolJobsController(
            self._core, self._tool_setup, self._compress, self._organise_controller
        )
        self._home = HomeController(self._core, self._file_jobs)
        # Late-bound to this object's own methods: `shutdown_voice` is what
        # a close shuts down (and what a test patches on the instance).
        self._window = WindowController(
            self._core, shutdown_voice=lambda: self.shutdown_voice(), drop_handler=self._on_file_drop
        )
        # Load the model now, on a background thread, rather than leaving it
        # to the first `get_voice_status()`. That call is made by every page
        # showing the mic indicator, and a bridge call that blocks for the
        # several seconds a load takes can be outlived by the page that made
        # it — see `VoiceEngine.warm_up`. Costs nothing when no model is
        # installed: the warm-up finds that out with a stat and stops.
        self._voice.warm_up()
        # Idle wake listening, if the reader has it on. Started here rather
        # than from the frontend so it survives navigation between pages —
        # the microphone should not close and reopen every time the reader
        # goes from Home into a document.
        self._voice_controller.apply_wake_activation(settings.get_voice_activation()["wake_phrase"])

    # Seams onto the shared state for the tests, under the names this class
    # used before the split. Private, so pywebview never exposes them.
    _doc = _shared("_core", "doc")
    _path = _shared("_core", "path")
    _voice = _shared("_core", "voice")
    _ocr = _shared("_core", "ocr")
    _jobs = _shared("_core", "jobs")
    _scratch = _shared("_core", "scratch")
    _estimates = _shared("_core", "estimates")
    _voice_viewport = _shared("_core.vocabulary", "viewport")
    _voice_context = _shared("_core.vocabulary", "context")
    _closing_confirmed = _shared("_window", "closing_confirmed")

    def _state(self) -> dict:
        return self._core.state()

    def _refresh_voice_vocabulary(self) -> None:
        self._core.vocabulary.refresh()

    # Where the voice engine, the OCR worker and the window report: this
    # object's own methods, which the tests also call directly.

    def _on_voice_result(self, result: dict) -> None:
        return self._voice_controller.on_voice_result(result)

    def _on_ocr_finished(self, job: ocr.OcrJob, status: str, payload: dict | None) -> None:
        return self._reader.on_ocr_finished(job, status, payload)

    def _on_file_drop(self, event: dict) -> None:
        return self._window.on_file_drop(event)

    # ---- Home: Recent and Favorites (bridge/home.py) ------------------------

    def get_recent_files(self) -> list[dict]:
        return self._home.get_recent_files()

    def get_thumbnails(self, paths: list[str]) -> dict[str, str | None]:
        return self._home.get_thumbnails(paths)

    def remove_recent_file(self, path: str) -> dict:
        return self._home.remove_recent_file(path)

    def undo_remove_recent_file(self) -> dict:
        return self._home.undo_remove_recent_file()

    def get_favorites(self) -> list[dict]:
        return self._home.get_favorites()

    def set_favorite(self, path: str, favorite: bool) -> dict:
        return self._home.set_favorite(path, favorite)

    def show_in_folder(self, path: str) -> dict:
        return self._home.show_in_folder(path)

    def show_open_document_in_folder(self) -> dict:
        return self._home.show_open_document_in_folder()

    # ---- Open and Save-As file browser (bridge/dialogs.py) ------------------

    def browse_directory(self, path: str | None = None, kind: str = dialog_browser.PDF_FILTER) -> dict:
        return self._dialogs.browse_directory(path, kind)

    def list_drives(self) -> dict:
        return self._dialogs.list_drives()

    def list_quick_access(self) -> dict:
        return self._dialogs.list_quick_access()

    # ---- Reader: document, pages, navigation, zoom, OCR, find (bridge/reader.py) ----

    def open_pdf_to_organise(self, path: str, password: str | None = None) -> dict:
        return self._reader.open_pdf_to_organise(path, password)

    def open_pdf(self, path: str, password: str | None = None) -> dict:
        return self._reader.open_pdf(path, password)

    def set_layout_mode(self, mode: str) -> dict:
        return self._reader.set_layout_mode(mode)

    def get_state(self) -> dict:
        return self._reader.get_state()

    def get_page_image(self, index: int, device_scale: float = 1.0) -> dict:
        return self._reader.get_page_image(index, device_scale)

    def get_page_sizes(self) -> list[dict]:
        return self._reader.get_page_sizes()

    def get_page_size_points(self, index: int) -> dict:
        return self._reader.get_page_size_points(index)

    def get_page_thumbnail(self, index: int, width: int) -> dict:
        return self._reader.get_page_thumbnail(index, width)

    def get_outline(self) -> dict:
        return self._reader.get_outline()

    def next_page(self) -> dict:
        return self._reader.next_page()

    def prev_page(self) -> dict:
        return self._reader.prev_page()

    def goto_page(self, index: int) -> dict:
        return self._reader.goto_page(index)

    def zoom_in(self) -> dict:
        return self._reader.zoom_in()

    def zoom_out(self) -> dict:
        return self._reader.zoom_out()

    def set_zoom(self, value: float) -> dict:
        return self._reader.set_zoom(value)

    def get_page_chars(self, page_index: int) -> dict:
        return self._reader.get_page_chars(page_index)

    def ocr_page(self, page_index: int) -> dict:
        return self._reader.ocr_page(page_index)

    def find_in_document(self, query: str, start_page: int = 0, end_page: int | None = None) -> dict:
        return self._reader.find_in_document(query, start_page, end_page)

    # ---- Highlights (bridge/highlights.py) ----------------------------------

    def highlight_chars(self, page_index: int, start_idx: int, end_idx: int) -> dict:
        return self._highlights.highlight_chars(page_index, start_idx, end_idx)

    def add_ink_stroke(self, page_index, points, straightened=None) -> dict:
        return self._highlights.add_ink_stroke(page_index, points, straightened)

    def get_highlight_options(self) -> dict:
        return self._highlights.get_highlight_options()

    def set_highlight_style(self, style) -> dict:
        return self._highlights.set_highlight_style(style)

    def set_highlight_opacity(self, style, pct) -> dict:
        return self._highlights.set_highlight_opacity(style, pct)

    def set_ink_width(self, width) -> dict:
        return self._highlights.set_ink_width(width)

    def get_highlight_palette(self) -> list[str]:
        return self._highlights.get_highlight_palette()

    def get_highlight_color(self) -> str:
        return self._highlights.get_highlight_color()

    def set_highlight_color(self, name: str) -> dict:
        return self._highlights.set_highlight_color(name)

    def get_page_highlights(self, page_index: int) -> dict:
        return self._highlights.get_page_highlights(page_index)

    def recolor_highlight(self, page_index: int, highlight_id: int, color: str) -> dict:
        return self._highlights.recolor_highlight(page_index, highlight_id, color)

    def set_highlight_appearance(self, page_index, highlight_id, opacity_pct=None, width=None, gesture=None) -> dict:
        return self._highlights.set_highlight_appearance(page_index, highlight_id, opacity_pct, width, gesture)

    def remove_highlight(self, page_index: int, highlight_id: int) -> dict:
        return self._highlights.remove_highlight(page_index, highlight_id)

    def undo_highlight(self) -> dict:
        return self._highlights.undo_highlight()

    def redo_highlight(self) -> dict:
        return self._highlights.redo_highlight()

    # ---- Organise pages (bridge/organise.py) --------------------------------

    def organise_begin(self) -> dict:
        return self._organise_controller.organise_begin()

    def organise_action(self, kind: str, args: dict) -> dict:
        return self._organise_controller.organise_action(kind, args)

    def organise_add_source(self, path: str, password: str | None = None) -> dict:
        return self._organise_controller.organise_add_source(path, password)

    def organise_forget_source(self, source: str) -> dict:
        return self._organise_controller.organise_forget_source(source)

    def organise_extract_setup(self, positions: list) -> dict:
        return self._organise_controller.organise_extract_setup(positions)

    def suggest_extract_name(self, positions: list, folder: str) -> dict:
        return self._organise_controller.suggest_extract_name(positions, folder)

    def organise_debug_timing(self, report: dict) -> dict:
        return self._organise_controller.organise_debug_timing(report)

    def organise_undo(self) -> dict:
        return self._organise_controller.organise_undo()

    def organise_redo(self) -> dict:
        return self._organise_controller.organise_redo()

    def organise_cancel(self) -> dict:
        return self._organise_controller.organise_cancel()

    def organise_thumbnail(self, source: str, index: int, width: int, turn: int = 0) -> dict:
        return self._organise_controller.organise_thumbnail(source, index, width, turn)

    def organise_done(self) -> dict:
        return self._organise_controller.organise_done()

    # ---- Save (bridge/save.py) ----------------------------------------------

    def get_save_dialog_start(self) -> dict:
        return self._save.get_save_dialog_start()

    def perform_save(self, mode: str, path: str | None = None) -> dict:
        return self._save.perform_save(mode, path)

    def describe_save_target(self, path: str) -> dict:
        return self._save.describe_save_target(path)

    # ---- File tools: set-up, destinations, inputs (bridge/file_tool_setup.py) ----

    def get_file_tool_menu(self) -> dict:
        return self._tool_setup.get_file_tool_menu()

    def file_tool_info(self, tool: str, source: str, password: str | None = None) -> dict:
        return self._tool_setup.file_tool_info(tool, source, password)

    def file_tool_analysis(self, source: str) -> dict:
        return self._tool_setup.file_tool_analysis(source)

    def cancel_file_tool_analysis(self) -> dict:
        return self._tool_setup.cancel_file_tool_analysis()

    def warm_file_tool_worker(self) -> dict:
        return self._tool_setup.warm_file_tool_worker()

    def forget_file_tool_passwords(self) -> dict:
        return self._tool_setup.forget_file_tool_passwords()

    def get_save_places(self, origin_folder: str) -> dict:
        return self._tool_setup.get_save_places(origin_folder)

    def remember_output_folder(self, folder: str) -> dict:
        return self._tool_setup.remember_output_folder(folder)

    def check_output_folder(self, folder: str) -> dict:
        return self._tool_setup.check_output_folder(folder)

    def suggest_tool_name(self, tool: str, source: str, folder: str, inputs=None) -> dict:
        return self._tool_setup.suggest_tool_name(tool, source, folder, inputs)

    def check_tool_destination(self, tool: str, source: str, folder: str, name: str, inputs=None) -> dict:
        return self._tool_setup.check_tool_destination(tool, source, folder, name, inputs)

    def parse_export_range(self, text: str, page_count: int) -> dict:
        return self._tool_setup.parse_export_range(text, page_count)

    def split_preview(self, source: str, mode: str, value: str, page_count: int) -> dict:
        return self._tool_setup.split_preview(source, mode, value, page_count)

    def describe_combine_inputs(self, paths: list[str]) -> list[dict]:
        return self._tool_setup.describe_combine_inputs(paths)

    def unlock_combine_input(self, path: str, password: str) -> dict:
        return self._tool_setup.unlock_combine_input(path, password)

    def forget_combine_passwords(self) -> dict:
        return self._tool_setup.forget_combine_passwords()

    def describe_convert_inputs(self, paths: list[str]) -> list[dict]:
        return self._tool_setup.describe_convert_inputs(paths)

    # ---- Compress (bridge/compress.py) --------------------------------------

    def start_compress_estimates(self, source: str, folder: str, session=None, mode=compress_plan.QUALITY) -> dict:
        return self._compress.start_compress_estimates(source, folder, session, mode)

    def stop_compress_estimates(self, discard: bool = True) -> dict:
        return self._compress.stop_compress_estimates(discard)

    def get_compress_estimates(self, session) -> dict:
        return self._compress.get_compress_estimates(session)

    def select_compress_limit(self, session, value=None, unit=None) -> dict:
        return self._compress.select_compress_limit(session, value, unit)

    def set_compress_mode(self, mode: str) -> dict:
        return self._compress.set_compress_mode(mode)

    # ---- File tool jobs (bridge/file_tool_jobs.py) --------------------------

    def start_file_job(self, tool: str, source: str, options: dict) -> dict:
        return self._file_jobs.start_file_job(tool, source, options)

    def cancel_file_job(self) -> dict:
        return self._file_jobs.cancel_file_job()

    def get_file_job(self) -> dict | None:
        return self._file_jobs.get_file_job()

    # ---- Settings (bridge/settings.py) --------------------------------------

    def get_save_behavior(self) -> str:
        return self._settings.get_save_behavior()

    def set_save_behavior(self, mode: str) -> None:
        return self._settings.set_save_behavior(mode)

    def get_reopen_behavior(self) -> str:
        return self._settings.get_reopen_behavior()

    def set_reopen_behavior(self, mode: str) -> None:
        return self._settings.set_reopen_behavior(mode)

    def get_theme(self) -> str:
        return self._settings.get_theme()

    def set_theme(self, name: str) -> None:
        return self._settings.set_theme(name)

    def get_recent_view(self) -> str:
        return self._settings.get_recent_view()

    def set_recent_view(self, mode: str) -> None:
        return self._settings.set_recent_view(mode)

    def get_reading_preferences(self) -> dict:
        return self._settings.get_reading_preferences()

    def set_default_view(self, mode: str) -> dict:
        return self._settings.set_default_view(mode)

    def set_progress_bar(self, on: bool) -> dict:
        return self._settings.set_progress_bar(on)

    def set_outline_open(self, is_open: bool) -> dict:
        return self._settings.set_outline_open(is_open)

    def set_outline_width(self, width: float) -> dict:
        return self._settings.set_outline_width(width)

    def get_onboarding_seen(self) -> bool:
        return self._settings.get_onboarding_seen()

    def set_onboarding_seen(self) -> None:
        return self._settings.set_onboarding_seen()

    # ---- Voice (bridge/voice.py) --------------------------------------------

    def highlight_by_voice(
        self, query: str, sentence: bool = False, color: str | None = None, style: str | None = None
    ) -> dict:
        return self._voice_controller.highlight_by_voice(query, sentence, color, style)

    def get_voice_activation(self) -> dict:
        return self._voice_controller.get_voice_activation()

    def set_voice_activation(self, push_to_talk: bool, wake_phrase: bool) -> dict:
        return self._voice_controller.set_voice_activation(push_to_talk, wake_phrase)

    def request_microphone(self) -> dict:
        return self._voice_controller.request_microphone()

    def get_command_reference(self) -> dict:
        return self._voice_controller.get_command_reference()

    def get_voice_status(self) -> dict:
        return self._voice_controller.get_voice_status()

    def start_listening(self, viewport: list[dict] | None = None) -> dict:
        return self._voice_controller.start_listening(viewport)

    def stop_listening(self) -> dict:
        return self._voice_controller.stop_listening()

    def update_voice_viewport(self, viewport: list[dict] | None = None) -> None:
        return self._voice_controller.update_voice_viewport(viewport)

    def set_voice_context(self, context: str) -> dict:
        return self._voice_controller.set_voice_context(context)

    def push_voice_context(self, context: str) -> dict:
        return self._voice_controller.push_voice_context(context)

    def pop_voice_context(self) -> dict:
        return self._voice_controller.pop_voice_context()

    def shutdown_voice(self) -> None:
        return self._voice_controller.shutdown_voice()

    # ---- Window: file drop, launch file, closing (bridge/window.py) ---------

    def shutdown_ocr(self) -> None:
        return self._window.shutdown_ocr()

    def bind_file_drop(self, window) -> None:
        return self._window.bind_file_drop(window)

    def on_page_loaded(self, window) -> None:
        return self._window.on_page_loaded(window)

    def sync_window_title(self) -> None:
        return self._core.sync_window_title()

    def open_file_from_launch(self, path: str) -> None:
        return self._window.open_file_from_launch(path)

    def notify_background_error(self) -> None:
        return self._window.notify_background_error()

    def handle_window_closing(self) -> bool | None:
        return self._window.handle_window_closing()

    def close_app(self) -> None:
        return self._window.close_app()
