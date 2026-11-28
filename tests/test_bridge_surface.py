"""The bridge's surface: exactly what the frontend can call on `Api`.

`Api` is the object handed to pywebview as `js_api`. pywebview exposes every
public method on it, by name, with its parameter names (`webview/util.py`'s
`get_functions`), and walks into any public attribute that is an object, which
would expose that object's methods too. The frontend calls these names with
`callApi("…")`, so a rename, a dropped method, a changed parameter or a new
public attribute changes the contract.

`BRIDGE_METHODS` was taken from `Api` before `api.py` was split into the
controllers under `lector/bridge/` (docs/ARCHITECTURE.md § "Bridge layout").
A method added on purpose is added here in the same change.
"""
import inspect
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lector import api as api_module  # noqa: E402
from lector.features.settings import store  # noqa: E402

# Every public bridge method, with its parameters after `self` as
# "name" or "name=<repr of default>".
BRIDGE_METHODS = {
    "add_ink_stroke": ('page_index', 'points', 'straightened=None'),
    "bind_file_drop": ('window',),
    "browse_directory": ('path=None', "kind='pdf'"),
    "cancel_file_job": (),
    "check_output_folder": ('folder',),
    "check_tool_destination": ('tool', 'source', 'folder', 'name', 'inputs=None'),
    "close_app": (),
    "describe_combine_inputs": ('paths',),
    "describe_convert_inputs": ('paths',),
    "describe_save_target": ('path',),
    "file_tool_info": ('tool', 'source', 'password=None'),
    "file_tool_analysis": ('source',),
    "cancel_file_tool_analysis": (),
    "warm_file_tool_worker": (),
    "forget_file_tool_passwords": (),
    "find_in_document": ('query', 'start_page=0', 'end_page=None'),
    "forget_combine_passwords": (),
    "get_command_reference": (),
    "get_compress_estimates": ('session',),
    "get_favorites": (),
    "get_file_job": (),
    "get_file_tool_menu": (),
    "get_highlight_color": (),
    "get_highlight_options": (),
    "get_highlight_palette": (),
    "get_onboarding_seen": (),
    "get_outline": (),
    "get_page_chars": ('page_index',),
    "get_page_highlights": ('page_index',),
    "get_page_image": ('index', 'device_scale=1.0'),
    "get_page_size_points": ('index',),
    "get_page_sizes": (),
    "get_page_thumbnail": ('index', 'width'),
    "get_reading_preferences": (),
    "get_recent_files": (),
    "get_recent_view": (),
    "get_reopen_behavior": (),
    "get_save_behavior": (),
    "get_save_dialog_start": (),
    "get_save_places": ('origin_folder',),
    "get_state": (),
    "get_theme": (),
    "get_thumbnails": ('paths',),
    "get_voice_activation": (),
    "get_voice_status": (),
    "goto_page": ('index',),
    "handle_window_closing": (),
    "highlight_by_voice": ('query', 'sentence=False', 'color=None', 'style=None'),
    "highlight_chars": ('page_index', 'start_idx', 'end_idx'),
    "list_drives": (),
    "list_quick_access": (),
    "next_page": (),
    "notify_background_error": (),
    "ocr_page": ('page_index',),
    "on_page_loaded": ('window',),
    "open_file_from_launch": ('path',),
    "open_pdf": ('path', 'password=None'),
    "open_pdf_to_organise": ('path', 'password=None'),
    "organise_action": ('kind', 'args'),
    "organise_add_source": ('path', 'password=None'),
    "organise_begin": (),
    "organise_cancel": (),
    "organise_debug_timing": ('report',),
    "organise_done": (),
    "organise_extract_setup": ('positions',),
    "organise_forget_source": ('source',),
    "organise_redo": (),
    "organise_thumbnail": ('source', 'index', 'width', 'turn=0'),
    "organise_undo": (),
    "parse_export_range": ('text', 'page_count'),
    "perform_save": ('mode', 'path=None'),
    "pop_voice_context": (),
    "prev_page": (),
    "push_voice_context": ('context',),
    "recolor_highlight": ('page_index', 'highlight_id', 'color'),
    "redo_highlight": (),
    "remember_output_folder": ('folder',),
    "remove_highlight": ('page_index', 'highlight_id'),
    "remove_recent_file": ('path',),
    "request_microphone": (),
    "select_compress_limit": ('session', 'value=None', 'unit=None'),
    "set_compress_mode": ('mode',),
    "set_default_view": ('mode',),
    "set_favorite": ('path', 'favorite'),
    "set_highlight_appearance": ('page_index', 'highlight_id', 'opacity_pct=None', 'width=None', 'gesture=None'),
    "set_highlight_color": ('name',),
    "set_highlight_opacity": ('style', 'pct'),
    "set_highlight_style": ('style',),
    "set_ink_width": ('width',),
    "set_layout_mode": ('mode',),
    "set_onboarding_seen": (),
    "set_outline_open": ('is_open',),
    "set_outline_width": ('width',),
    "set_progress_bar": ('on',),
    "set_recent_view": ('mode',),
    "set_reopen_behavior": ('mode',),
    "set_save_behavior": ('mode',),
    "set_theme": ('name',),
    "set_voice_activation": ('push_to_talk', 'wake_phrase'),
    "set_voice_context": ('context',),
    "set_zoom": ('value',),
    "show_in_folder": ('path',),
    "show_open_document_in_folder": (),
    "shutdown_ocr": (),
    "sync_window_title": (),
    "shutdown_voice": (),
    "split_preview": ('source', 'mode', 'value', 'page_count'),
    "start_compress_estimates": ('source', 'folder', 'session=None', "mode='quality'"),
    "start_file_job": ('tool', 'source', 'options'),
    "start_listening": ('viewport=None',),
    "stop_compress_estimates": ('discard=True',),
    "stop_listening": (),
    "suggest_extract_name": ('positions', 'folder'),
    "suggest_tool_name": ('tool', 'source', 'folder', 'inputs=None'),
    "undo_highlight": (),
    "undo_remove_recent_file": (),
    "unlock_combine_input": ('path', 'password'),
    "update_voice_viewport": ('viewport=None',),
    "zoom_in": (),
    "zoom_out": (),
}


def _parameters(function) -> tuple[str, ...]:
    params = list(inspect.signature(function).parameters.values())[1:]
    return tuple(p.name if p.default is inspect.Parameter.empty else f"{p.name}={p.default!r}" for p in params)


class BridgeSurfaceTests(unittest.TestCase):
    def test_the_public_methods_are_exactly_the_snapshot(self):
        public = {name for name in dir(api_module.Api) if not name.startswith("_")}
        self.assertEqual(sorted(public - set(BRIDGE_METHODS)), [], "new public names on Api")
        self.assertEqual(sorted(set(BRIDGE_METHODS) - public), [], "bridge methods missing from Api")

    def test_each_method_keeps_its_parameters(self):
        for name, expected in BRIDGE_METHODS.items():
            with self.subTest(method=name):
                self.assertEqual(_parameters(getattr(api_module.Api, name)), expected)

    def test_a_live_api_exposes_nothing_but_those_methods(self):
        # What pywebview actually walks is the instance: a public attribute
        # set in __init__ (a controller, say) would put its methods on the
        # bridge under a dotted name.
        with tempfile.TemporaryDirectory(prefix="lector-settings-") as folder, mock.patch.object(
            store, "_settings_path", lambda: Path(folder) / "settings.json"
        ):
            api = api_module.Api()
            try:
                for name in dir(api):
                    if name.startswith("_"):
                        continue
                    with self.subTest(attribute=name):
                        self.assertIn(name, BRIDGE_METHODS)
                        self.assertTrue(inspect.ismethod(getattr(api, name)))
            finally:
                api.shutdown_voice()
                api.shutdown_ocr()


if __name__ == "__main__":
    unittest.main()
