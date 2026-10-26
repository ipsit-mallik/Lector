"""Two things the code base must not grow without a decision being made first.

PyMuPDF stays in the modules that use it today. `docs/ARCHITECTURE.md` gives
`reading/` the rendering and text layer and `annotations/` the write layer, and
PyMuPDF's licence (AGPL or Artifex commercial) is still to be settled before
anything is packaged, so every new module that leans on it makes that choice
harder to change.

Nothing in the app talks to the network. The Settings footer says Lector is
fully offline; the only sockets are the frontend server on 127.0.0.1 and the
single-instance pipe. `scripts/` downloads the speech models and is not part of
the app, so it is not scanned.

The imports are read with `ast`, not run, so an import tucked inside a function
counts the same as one at the top, and so does `importlib.import_module("...")`
with a written-out name.
"""

import ast
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
SOURCES = sorted((SRC / "lector").rglob("*.py"))

PYMUPDF_NAMES = {"pymupdf", "fitz"}
PYMUPDF_ALLOWED = {
    "lector/features/annotations/highlighter.py",
    "lector/features/home/recent.py",
    "lector/features/reading/document.py",
    "lector/features/reading/ocr.py",
    "lector/features/reading/search.py",
}

# No module may import these, wherever it is.
NETWORK_FORBIDDEN = {
    "urllib.request",
    "http.client",
    "requests",
    "httpx",
    "aiohttp",
    "ftplib",
    "smtplib",
}
# These open sockets themselves, so each is allowed only where it is expected.
SOCKET_ALLOWED = {
    "socket": {"lector/shared/frontend_server.py", "lector/shared/single_instance.py"},
    "socketserver": {"lector/shared/frontend_server.py"},
    "http.server": {"lector/shared/frontend_server.py"},
    "multiprocessing.connection": {"lector/shared/single_instance.py"},
}


def imported_modules(source: str) -> set[str]:
    """Every module `source` imports, each dotted parent included, so
    `import urllib.request` also counts as importing `urllib`."""
    names = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            # `from urllib import request` imports urllib.request.
            names.add(node.module)
            names.update(f"{node.module}.{alias.name}" for alias in node.names)
        elif isinstance(node, ast.Call) and _is_import_by_name(node):
            names.add(node.args[0].value)
    return {".".join(name.split(".")[: i + 1]) for name in names for i in range(name.count(".") + 1)}


def _is_import_by_name(call: ast.Call) -> bool:
    func = call.func
    named = (isinstance(func, ast.Name) and func.id in ("__import__", "import_module")) or (
        isinstance(func, ast.Attribute) and func.attr == "import_module"
    )
    return named and bool(call.args) and isinstance(call.args[0], ast.Constant) and isinstance(call.args[0].value, str)


def _imports_by_file() -> dict[str, set[str]]:
    return {path.relative_to(SRC).as_posix(): imported_modules(path.read_text(encoding="utf-8")) for path in SOURCES}


class ScannerTests(unittest.TestCase):
    """The checks below are only worth something if they can see the imports."""

    def test_the_scan_covers_the_app_package(self):
        files = _imports_by_file()
        self.assertGreater(len(files), 20)
        self.assertIn("lector/api.py", files)

    def test_every_allowed_pymupdf_module_is_seen_importing_it(self):
        files = _imports_by_file()
        for name in sorted(PYMUPDF_ALLOWED):
            with self.subTest(file=name):
                self.assertTrue(PYMUPDF_NAMES & files[name])

    def test_every_allowed_socket_import_is_seen_in_a_file_allowed_it(self):
        # An allowance is not a requirement: single_instance.py may import
        # socket (macOS uses a Unix socket) but does not today.
        files = _imports_by_file()
        for module, allowed in SOCKET_ALLOWED.items():
            with self.subTest(module=module):
                self.assertTrue(any(module in files[name] for name in allowed))

    def test_imports_inside_functions_and_by_name_are_seen(self):
        source = (
            "import importlib\n"
            "def later():\n"
            "    import fitz\n"
            "    from urllib import request\n"
            "    importlib.import_module('httpx')\n"
            "    __import__('socket')\n"
        )
        self.assertLessEqual({"fitz", "urllib", "urllib.request", "httpx", "socket"}, imported_modules(source))

    def test_a_relative_import_is_not_mistaken_for_a_library(self):
        self.assertEqual(imported_modules("from .requests import x\n"), set())


class PyMuPDFStaysWhereItIsTests(unittest.TestCase):
    def test_only_the_five_known_modules_import_pymupdf(self):
        for name, imports in _imports_by_file().items():
            if name in PYMUPDF_ALLOWED:
                continue
            with self.subTest(file=name):
                found = sorted(PYMUPDF_NAMES & imports)
                self.assertEqual(
                    found, [],
                    f"{name} imports {', '.join(found)}: decide in ARCHITECTURE.md "
                    "before spreading PyMuPDF further, then add the file to "
                    "PYMUPDF_ALLOWED here",
                )


class NoNetworkTests(unittest.TestCase):
    def test_no_module_imports_a_network_library(self):
        for name, imports in _imports_by_file().items():
            with self.subTest(file=name):
                found = sorted(NETWORK_FORBIDDEN & imports)
                self.assertEqual(
                    found, [],
                    f"{name} imports {', '.join(found)}: Lector is fully offline "
                    "(the Settings footer says so), so network use is a scope change",
                )

    def test_sockets_are_opened_only_by_the_frontend_server_and_single_instance_pipe(self):
        for name, imports in _imports_by_file().items():
            for module, allowed in SOCKET_ALLOWED.items():
                if name in allowed:
                    continue
                with self.subTest(file=name, module=module):
                    self.assertNotIn(
                        module, imports,
                        f"{name} imports {module}: only {', '.join(sorted(allowed))} "
                        "may, and only on this machine (127.0.0.1 or a local pipe)",
                    )


if __name__ == "__main__":
    unittest.main()
