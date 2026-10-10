"""Lector serves its own pages, on a port the OS picks fresh for each launch.

pywebview's built-in server always takes port 42001, and on Windows a second
process can bind a port another already holds, so a second copy of Lector (or
any pywebview app) quietly got its pages from the first. `FrontendServer` binds
127.0.0.1 port 0 with exclusive use, so every launch has a port of its own.

That makes the page's origin different on every launch, so nothing may depend on
web storage surviving a restart. The saved theme used to be mirrored there for
the first paint; the server now writes it into each page's `<html data-theme>`
as it sends it, so the first frame is in the right theme without a bridge call.
"""

import http.client
import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lector.shared import frontend_server  # noqa: E402
from lector.shared.frontend_server import FrontendServer, with_page_state, with_theme  # noqa: E402

FRONTEND = Path(__file__).resolve().parents[1] / "frontend"
PAGES = ("index.html", "pages/settings.html", "pages/reading.html", "pages/onboarding.html")
PAGE = '<!DOCTYPE html>\n<html lang="en" data-theme="light">\n<head></head>\n<body>hi</body>\n</html>\n'


class WithThemeTest(unittest.TestCase):
    def test_sets_the_html_elements_theme(self):
        self.assertIn('<html lang="en" data-theme="dark">', with_theme(PAGE, "dark"))

    def test_only_the_html_element_changes(self):
        page = PAGE.replace("<body>hi", '<body><span data-theme="sepia">hi</span>')
        out = with_theme(page, "dark")
        self.assertIn('<span data-theme="sepia">', out)
        self.assertEqual(out.replace('data-theme="dark"', 'data-theme="light"', 1), page)

    def test_an_unknown_theme_is_served_as_light(self):
        self.assertIn('<html lang="en" data-theme="light">', with_theme(PAGE, '"><script>'))


HOME = (
    '<!DOCTYPE html>\n<html lang="en" data-theme="light" data-recent-view="grid" '
    'data-recent-count="" data-favorites-count="">\n<head></head>\n'
    '<body><p data-recent-view="grid"></p></body>\n</html>\n'
)


class WithPageStateTest(unittest.TestCase):
    """Home's view and file counts, written into its <html> as it is sent, so the
    page can draw the right placeholder before any bridge call."""

    def test_fills_the_attributes_the_html_element_declares(self):
        out = with_page_state(HOME, {"data-recent-view": "list", "data-recent-count": "7"})
        self.assertIn('data-recent-view="list" data-recent-count="7" data-favorites-count=""', out)

    def test_only_the_html_element_changes(self):
        out = with_page_state(HOME, {"data-recent-view": "list"})
        self.assertIn('<p data-recent-view="grid">', out)

    def test_a_page_that_declares_none_is_left_alone(self):
        self.assertEqual(with_page_state(PAGE, {"data-recent-view": "list"}), PAGE)

    def test_values_are_written_as_text_never_markup(self):
        out = with_page_state(HOME, {"data-recent-count": '"><script>alert(1)</script>'})
        self.assertNotIn("<script>", out)
        self.assertIn('data-recent-count="&quot;&gt;&lt;script&gt;', out)


class EveryPageCanBeThemedTest(unittest.TestCase):
    def test_every_page_carries_a_default_the_server_replaces(self):
        for name in PAGES:
            with self.subTest(page=name):
                html = (FRONTEND / name).read_text(encoding="utf-8")
                self.assertRegex(html, r'<html\b[^>]*\bdata-theme="light"')
                self.assertIn('data-theme="sepia"', with_theme(html, "sepia")[: html.index("<head")])

    def test_no_page_paints_its_theme_from_web_storage(self):
        for name in PAGES:
            with self.subTest(page=name):
                self.assertNotIn("lector-theme", (FRONTEND / name).read_text(encoding="utf-8"))


class _Served(unittest.TestCase):
    """One server per class: stopping one waits out its poll interval."""

    @classmethod
    def setUpClass(cls):
        tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(tmp.cleanup)
        base = Path(tmp.name)
        cls.root = base / "frontend"
        (cls.root / "pages").mkdir(parents=True)
        (cls.root / "index.html").write_text(PAGE, encoding="utf-8")
        (cls.root / "home.html").write_text(HOME, encoding="utf-8")
        (cls.root / "pages" / "a.css").write_text("body { color: red; }", encoding="utf-8")
        (base / "secret.txt").write_text("not for the page", encoding="utf-8")
        cls.saved = {"theme": "dark"}
        cls.server = FrontendServer(
            cls.root, lambda: cls.saved["theme"], lambda: cls.saved["page_state"]()
        )
        cls.server.start()
        cls.addClassCleanup(cls.server.stop)

    def setUp(self):
        self.saved["theme"] = "dark"
        self.saved["page_state"] = lambda: {"data-recent-view": "list", "data-recent-count": "4"}

    def get(self, path):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.port, timeout=5)
        self.addCleanup(conn.close)
        conn.request("GET", path)
        resp = conn.getresponse()
        return resp, resp.read()


class FrontendServerTest(_Served):
    def test_pages_arrive_already_in_the_saved_theme(self):
        resp, body = self.get("/index.html")
        self.assertEqual(resp.status, 200)
        self.assertIn(b'<html lang="en" data-theme="dark">', body)
        self.assertEqual(resp.getheader("Content-Type"), "text/html; charset=utf-8")
        self.assertEqual(int(resp.getheader("Content-Length")), len(body))

    def test_the_theme_is_read_for_every_page_not_once(self):
        self.get("/index.html")
        self.saved["theme"] = "sepia"
        self.assertIn(b'data-theme="sepia"', self.get("/index.html")[1])

    def test_pages_arrive_with_the_page_state_filled_in(self):
        body = self.get("/home.html")[1]
        self.assertIn(b'data-theme="dark" data-recent-view="list" data-recent-count="4"', body)

    def test_a_page_state_that_fails_leaves_the_pages_defaults(self):
        def broken():
            raise OSError("settings.json is locked")

        self.saved["page_state"] = broken
        resp, body = self.get("/home.html")
        self.assertEqual(resp.status, 200)
        self.assertIn(b'data-recent-view="grid" data-recent-count=""', body)

    def test_other_files_are_sent_as_they_are(self):
        resp, body = self.get("/pages/a.css")
        self.assertEqual(resp.status, 200)
        self.assertEqual(body, b"body { color: red; }")

    def test_nothing_is_cached(self):
        for path in ("/index.html", "/pages/a.css"):
            with self.subTest(path=path):
                self.assertEqual(self.get(path)[0].getheader("Cache-Control"), "no-store")

    def test_nothing_outside_the_frontend_folder_is_reachable(self):
        for path in ("/../secret.txt", "/pages/../../secret.txt", "/%2e%2e/secret.txt"):
            with self.subTest(path=path):
                resp, body = self.get(path)
                self.assertEqual(resp.status, 404)
                self.assertNotIn(b"not for the page", body)

    def test_folders_are_not_listed(self):
        self.assertEqual(self.get("/pages/")[0].status, 404)

    def test_a_missing_file_is_404(self):
        self.assertEqual(self.get("/nope.js")[0].status, 404)

    def test_url_names_a_page_on_this_server(self):
        self.assertEqual(self.server.url("index.html"), f"http://127.0.0.1:{self.server.port}/index.html")


class PortTest(_Served):
    def test_listens_on_loopback_only(self):
        self.assertEqual(self.server.address[0], "127.0.0.1")

    def test_each_server_gets_its_own_port(self):
        other = FrontendServer(self.root, lambda: "light")
        other.start()
        self.addCleanup(other.stop)
        self.assertNotEqual(other.port, self.server.port)
        self.assertNotEqual(self.server.port, 42001)

    def test_a_port_in_use_cannot_be_shared(self):
        # The fault this replaces: on Windows, SO_REUSEADDR let a second server
        # bind a port the first held, and connections kept reaching the first.
        self.assertFalse(frontend_server._Server.allow_reuse_address)


class WiringTest(unittest.TestCase):
    MAIN_PY = (Path(__file__).resolve().parents[1] / "src" / "lector" / "__main__.py").read_text(encoding="utf-8")

    def test_the_window_opens_on_lectors_server_with_the_saved_theme(self):
        body = self.MAIN_PY[self.MAIN_PY.index("def main("):]
        self.assertRegex(
            body, r"FrontendServer\(FRONTEND_DIR, settings\.get_theme, home_recent\.page_state\)"
        )
        self.assertIn('url=server.url("index.html")', body)

    def test_pywebviews_own_server_is_not_started(self):
        self.assertNotIn("http_server=True", self.MAIN_PY)
        self.assertNotIn("http_port", self.MAIN_PY)
        self.assertNotRegex(self.MAIN_PY, re.compile(r"url=str\(FRONTEND_DIR"))


if __name__ == "__main__":
    unittest.main()
