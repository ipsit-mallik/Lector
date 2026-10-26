"""The model downloads in scripts/ install only the exact files they were
pinned to.

Each file is fed to native code (Tesseract, onnxruntime, Vosk), so each script
names one release, not a moving branch or "whatever PyPI calls latest", and
checks the download against a SHA-256 written there before anything is
installed. A download that doesn't match leaves nothing behind.

No network: `urlopen` is replaced with one that serves bytes made here.
"""

import hashlib
import importlib.util
import io
import re
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"_script_{name}", SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Response(io.BytesIO):
    def __init__(self, data: bytes):
        super().__init__(data)
        self.headers = {"Content-Length": str(len(data))}


def _serving(data: bytes):
    return mock.patch("urllib.request.urlopen", side_effect=lambda *a, **k: _Response(data))


def _zip(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return buffer.getvalue()


class _Download:
    """Mixed into each script's test case below."""

    SCRIPT = ""
    HASH_NAME = ""

    def setUp(self):
        self.script = _load(self.SCRIPT)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dest = Path(tmp.name) / "assets" / self.script.DEST.name
        for patcher in (mock.patch.object(self.script, "DEST", self.dest), mock.patch("builtins.print")):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_a_download_that_does_not_match_the_pinned_hash_installs_nothing(self):
        with _serving(self.download()):
            with self.assertRaisesRegex(RuntimeError, "SHA-256"):
                self.script.fetch(force=True)
        self.assertFalse(self.installed())

    def test_the_pinned_file_is_installed(self):
        data = self.download()
        with _serving(data), mock.patch.object(self.script, self.HASH_NAME, hashlib.sha256(data).hexdigest()):
            self.script.fetch(force=True)
        self.assertTrue(self.installed())

    def test_the_pin_is_a_full_sha256(self):
        self.assertRegex(getattr(self.script, self.HASH_NAME), r"^[0-9a-f]{64}$")


class TessdataTest(_Download, unittest.TestCase):
    SCRIPT = "fetch_tessdata"
    HASH_NAME = "SHA256"

    def download(self):
        return b"traineddata"

    def installed(self):
        return self.dest.exists()

    def test_it_names_a_release_not_a_branch(self):
        self.assertRegex(self.script.URL, r"/tessdata_fast/raw/\d+\.\d+\.\d+/eng\.traineddata$")


class VadModelTest(_Download, unittest.TestCase):
    SCRIPT = "fetch_vad_model"
    HASH_NAME = "WHEEL_SHA256"

    def download(self):
        return _zip({self.script.MODEL_PATH_IN_WHEEL: b"onnx graph"})

    def installed(self):
        return self.dest.exists()

    def test_it_names_one_wheel_not_the_latest_release(self):
        self.assertRegex(self.script.WHEEL_URL, r"/silero_vad-\d+\.\d+\.\d+-py3-none-any\.whl$")
        self.assertNotIn("pypi.org/pypi/", (SCRIPTS / "fetch_vad_model.py").read_text(encoding="utf-8"))


class VoskModelTest(_Download, unittest.TestCase):
    SCRIPT = "fetch_vosk_model"
    HASH_NAME = "MODEL_SHA256"

    def download(self):
        return _zip({f"vosk-model-test/{marker}/file": b"x" for marker in self.script.MODEL_MARKERS})

    def installed(self):
        return self.script.is_installed(self.dest)

    def test_it_names_one_versioned_archive(self):
        self.assertRegex(self.script.MODEL_URL, r"/vosk-model-[\w.-]+-\d+\.\d+[\w.-]*\.zip$")


class EveryScriptTest(unittest.TestCase):
    def test_every_download_script_checks_a_pinned_hash(self):
        for path in sorted(SCRIPTS.glob("fetch_*.py")):
            with self.subTest(script=path.name):
                text = path.read_text(encoding="utf-8")
                self.assertRegex(text, re.compile(r'^[A-Z_]*SHA256 = "[0-9a-f]{64}"$', re.M))
                self.assertIn("hashlib.sha256", text)


if __name__ == "__main__":
    unittest.main()
