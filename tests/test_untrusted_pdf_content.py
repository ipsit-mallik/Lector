"""A PDF's links and actions are never followed (hardening H5).

A PDF can carry web links, "launch this program" links, JavaScript that runs
on open, and bookmarks that point at a website. Lector reads none of them:
pages reach the screen as pictures, and nothing in `src/lector` asks MuPDF
for a link or an action. These tests keep it that way. The first opens such
a PDF and does everything the reader can do with it while every way of
starting a program or a browser is watched; the second fails if code that
could follow a link or start a program appears anywhere new in `src/lector`.

If that second test fails because a feature really needs to follow a link,
route it through an explicit confirmation that shows the reader the
destination first, then add the call here on purpose.
"""
import ast
import importlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import pymupdf  # noqa: E402  # PyMuPDF: builds the hostile PDF

from lector import api as api_module  # noqa: E402
from lector.features.settings import store  # noqa: E402

SRC = ROOT / "src" / "lector"

# What could start a program or a browser, as (module, attribute).
LAUNCHERS = {
    ("subprocess", "Popen"),
    ("subprocess", "run"),
    ("subprocess", "call"),
    ("subprocess", "check_call"),
    ("subprocess", "check_output"),
    ("subprocess", "getoutput"),
    ("subprocess", "getstatusoutput"),
    ("os", "system"),
    ("os", "startfile"),
    ("os", "popen"),
    ("webbrowser", "open"),
    ("webbrowser", "open_new"),
    ("webbrowser", "open_new_tab"),
}

# MuPDF's ways in to a PDF's links, actions, embedded files and forms.
PDF_ACTION_APIS = {
    "get_links", "load_links", "first_link", "links", "resolve_link",
    "embfile_get", "embfile_names", "widgets", "first_widget",
    "LINK_URI", "LINK_LAUNCH",
}

# The one place Lector starts another program: "Show in folder" on a Recent
# entry, which opens the file manager on a file already in the list.
ALLOWED = {("features/home/recent.py", "reveal_in_file_manager")}

# Modules whose only use is starting programs or browsers, and the files that
# may import them at all.
LAUNCHER_MODULES = {"subprocess", "webbrowser", "pty"}
LAUNCHER_IMPORTS_ALLOWED = {"features/home/recent.py"}
# What `from os import …` may never bring in, and names that start a program
# however they are reached (os.spawnv, os.execv, ShellExecuteW, …).
OS_LAUNCHERS = {"system", "startfile", "popen"}
LAUNCHER_NAME_PREFIXES = ("spawnl", "spawnv", "execl", "execv", "posix_spawn", "ShellExecute")


def _hostile_pdf(path: Path) -> Path:
    doc = pymupdf.open()
    for i in range(2):
        page = doc.new_page()
        page.insert_text((72, 72), f"Click here to win, page {i + 1}")
        page.insert_link({"kind": pymupdf.LINK_URI, "from": pymupdf.Rect(72, 60, 300, 80),
                          "uri": "https://example.invalid/track"})
        page.insert_link({"kind": pymupdf.LINK_LAUNCH, "from": pymupdf.Rect(72, 90, 300, 110),
                          "file": "calc.exe"})
    doc.set_toc([[1, "Visit the site", 1], [1, "Page two", 2]])
    # The first bookmark points at a website instead of a page.
    outlines = int(doc.xref_get_key(doc.pdf_catalog(), "Outlines")[1].split()[0])
    first = int(doc.xref_get_key(outlines, "First")[1].split()[0])
    doc.xref_set_key(first, "Dest", "null")
    doc.xref_set_key(first, "A", "<</S/URI/URI(https://example.invalid/outline)>>")
    # JavaScript that runs when the document opens, in a viewer that runs it.
    doc.xref_set_key(doc.pdf_catalog(), "OpenAction",
                     "<</S/JavaScript/JS(app.launchURL\\('https://example.invalid/js'\\))>>")
    doc.save(path)
    doc.close()
    return path


class NothingIsFollowedTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="lector-hostile-")
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        patcher = mock.patch.object(store, "_settings_path", lambda: self.dir / "settings.json")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.api = api_module.Api()
        self.addCleanup(self.api.shutdown_voice)
        self.addCleanup(self.api._doc.close)
        self.calls: list[str] = []
        for module_name, attr in sorted(LAUNCHERS):
            module = importlib.import_module(module_name)
            if not hasattr(module, attr):
                continue  # os.startfile exists on Windows only
            patcher = mock.patch.object(module, attr, side_effect=self._recorder(f"{module_name}.{attr}"))
            patcher.start()
            self.addCleanup(patcher.stop)

    def _recorder(self, name: str):
        def called(*args, **kwargs):
            self.calls.append(name)
        return called

    def test_the_pdf_really_carries_them(self):
        doc = pymupdf.open(_hostile_pdf(self.dir / "hostile.pdf"))
        self.addCleanup(doc.close)
        # Read raw: PyMuPDF reports a Launch action back as LINK_GOTOR.
        actions = " ".join(doc.xref_object(xref, compressed=True) for xref, *_ in doc[0].annot_xrefs())
        self.assertIn("/S/URI", actions)
        self.assertIn("/S/Launch", actions)
        self.assertIn("JavaScript", doc.xref_get_key(doc.pdf_catalog(), "OpenAction")[1])
        self.assertEqual(doc.get_toc(simple=False)[0][3].get("uri"), "https://example.invalid/outline")

    def test_reading_a_pdf_full_of_links_and_actions_starts_nothing(self):
        path = _hostile_pdf(self.dir / "hostile.pdf")
        state = self.api.open_pdf(str(path))
        self.assertTrue(state["is_open"])
        for index in range(state["page_count"]):
            self.api.get_page_image(index)
            self.api.get_page_thumbnail(index, 128)
        self.api.get_page_sizes()
        outline = self.api.get_outline()
        self.api.find_in_document("win")
        self.api.get_recent_files()
        self.api.get_thumbnails([str(path)])
        self.assertEqual(self.calls, [])
        # The website bookmark is shown as a title, never as somewhere to go.
        self.assertNotIn("example.invalid", repr(outline))


def _innermost_function(tree: ast.AST) -> dict[int, str]:
    """Each node's innermost enclosing function name, by node id. `ast.walk`
    reaches an outer function before the ones inside it, so inner names
    overwrite outer ones."""
    owner: dict[int, str] = {}
    for function in ast.walk(tree):
        if isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for node in ast.walk(function):
                owner[id(node)] = function.name
    return owner


class NoCodeCanFollowALinkTests(unittest.TestCase):
    def test_only_show_in_folder_starts_another_program_and_nothing_reads_pdf_actions(self):
        found = set()
        for path in SRC.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            owner = _innermost_function(tree)
            for node in ast.walk(tree):
                if not isinstance(node, ast.Attribute):
                    continue
                base = node.value.id if isinstance(node.value, ast.Name) else None
                if (base, node.attr) in LAUNCHERS or node.attr in PDF_ACTION_APIS:
                    found.add((path.relative_to(SRC).as_posix(), owner.get(id(node), "<module>")))
        self.assertEqual(
            found - ALLOWED, set(),
            "Code that could follow a PDF's link or start a program. Route it "
            "through an explicit confirmation that shows the destination first.",
        )
        self.assertEqual(found, ALLOWED, "the check no longer sees Show in folder's launcher")

    def test_no_launcher_is_reached_through_an_import_or_another_name(self):
        """What the attribute scan above can't see: `from subprocess import
        Popen`, `import subprocess as sp`, `from os import system`, and the
        os.spawn*/os.exec* and ShellExecute families."""
        problems = []
        for path in SRC.rglob("*.py"):
            relative = path.relative_to(SRC).as_posix()
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Import):
                    modules = {alias.name.split(".")[0] for alias in node.names}
                elif isinstance(node, ast.ImportFrom):
                    modules = {(node.module or "").split(".")[0]}
                    if node.module == "os":
                        names = {alias.name for alias in node.names}
                        bad = names & OS_LAUNCHERS | {n for n in names if n.startswith(LAUNCHER_NAME_PREFIXES)}
                        problems += [f"{relative}: from os import {n}" for n in sorted(bad)]
                elif isinstance(node, ast.Attribute) and node.attr.startswith(LAUNCHER_NAME_PREFIXES):
                    problems.append(f"{relative}: .{node.attr}")
                    continue
                else:
                    continue
                for module in sorted(modules & LAUNCHER_MODULES):
                    if relative not in LAUNCHER_IMPORTS_ALLOWED:
                        problems.append(f"{relative}: imports {module}")
        self.assertEqual(
            problems, [],
            "Code that could start a program. Route it through an explicit "
            "confirmation that shows the destination first.",
        )


if __name__ == "__main__":
    unittest.main()
