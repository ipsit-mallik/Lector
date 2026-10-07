"""The web view must not serve the app's own pages from a cache left by an
earlier launch.

pywebview serves frontend/ from a local server that sends `Last-Modified` and
`ETag` but no `Cache-Control` (it sets one, then returns a response object that
replaces it). Chromium then treats a cached file as fresh for a tenth of the time
since it was last modified, and the web view keeps its cache between launches
(`storage_path` in `__main__`). So a page that had gone unedited for hours kept
being served from the cache after an edit or an update: the window showed the old
Home while the server was serving the new one.

`--disable-http-cache` was tried first and does nothing in WebView2 (measured: a
second launch still answered every file, `index.html` included, from the disk
cache), so `clear_http_cache` removes the cache folders before the web view starts.
Everything is read from disk on this machine, so there is nothing for a cache to
save. What else the profile holds (`Local Storage` carries the saved theme) must
survive.
"""

import logging
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lector import __main__ as app_main  # noqa: E402


def _profile(root: Path) -> Path:
    """A profile folder laid out as WebView2 writes it, with a file in each place."""
    default = root / "EBWebView" / "Default"
    for folder in ("Cache/Cache_Data", "Code Cache/js", "Local Storage/leveldb"):
        (default / folder).mkdir(parents=True)
        (default / folder / "entry").write_text("x", encoding="utf-8")
    return root


class ClearHttpCacheTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.profile = _profile(Path(tmp.name))
        self.default = self.profile / "EBWebView" / "Default"

    def test_removes_the_cached_pages_and_compiled_scripts(self):
        app_main.clear_http_cache(self.profile)
        self.assertFalse((self.default / "Cache").exists())
        self.assertFalse((self.default / "Code Cache").exists())

    def test_keeps_local_storage_so_the_saved_theme_survives(self):
        app_main.clear_http_cache(self.profile)
        self.assertTrue((self.default / "Local Storage" / "leveldb" / "entry").exists())

    def test_a_first_launch_with_no_profile_is_not_an_error(self):
        app_main.clear_http_cache(self.profile / "does-not-exist")

    def test_a_cache_that_cannot_be_removed_is_reported_and_does_not_stop_startup(self):
        with mock.patch.object(app_main.shutil, "rmtree", side_effect=PermissionError("in use")):
            with self.assertLogs(app_main.log, level=logging.WARNING) as logged:
                app_main.clear_http_cache(self.profile)
        self.assertIn("in use", "\n".join(logged.output))

    def test_main_clears_the_cache_before_the_web_view_starts(self):
        source = Path(app_main.__file__).read_text(encoding="utf-8")
        body = source[source.index("def main()"):]
        self.assertIn("clear_http_cache(", body)
        self.assertLess(body.index("clear_http_cache("), body.index("webview.start("))


if __name__ == "__main__":
    unittest.main()
