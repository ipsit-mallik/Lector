"""Compress's two tabs (docs/PRD.md § "Post-v1: file tools" → Compress):
one quality ladder whose steps are compressed in advance and shared by both
tabs, the preset size limits checked against it, black-and-white pages, the
temporary files, and names that don't stack suffixes.

Held here:
- the plan: the ladder, 1 KB = 1,000 bytes, a step fits at 97% of a limit
  or less, the best step under a limit and when that answer is settled,
  "too small", what each step is called, and which steps make the same file;
- Quality: the estimates fill in, and a level under 5% smaller is "No
  change"; Compress on a finished estimate does no second compression (the
  engine's calls are counted), and one still running is waited for;
- Size limit: every step is made, smallest first, and kept across tabs; a
  file with no scanned page or no image makes fewer; Compress moves the
  chosen step's file into place with no second compression; the safety net
  holds a step that doesn't fit after all; the bridge refuses a limit that
  isn't settled, is too small, or is at or above the file's size;
- temporary files go when the dialog closes, when a job takes one, at quit
  and at the next start;
- black-and-white changes only images that cover most of a page;
- no tool's default name stacks its own suffix.

The engine runs in this process through `InlineWorker`, which serializes
calls as the real worker process does; the Api tests spawn the real worker.
"""
import json
import os
import random
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import Future
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pymupdf  # noqa: E402

import file_tools_support as fx  # noqa: E402
import pdf_support  # noqa: E402
from lector import api as api_module  # noqa: E402
from lector.features.file_tools import compress_plan, destinations, engine, jobs, messages, tools  # noqa: E402
from lector.features.file_tools.estimates import CompressEstimates  # noqa: E402
from lector.features.file_tools.scratch import PREFIX, ScratchFiles, clean_leftovers  # noqa: E402
from lector.features.reading import ocr  # noqa: E402
from lector.features.settings import store  # noqa: E402

WAIT_S = 120


class InlineWorker:
    """`OcrRunner.call` run here, one call at a time, each one recorded."""

    def __init__(self):
        self.calls: list[str] = []
        self._lock = threading.Lock()

    def call(self, fn, args=(), timeout=0, abort=None) -> Future:
        future: Future = Future()
        with self._lock:
            if abort is not None and abort.is_set():
                future.set_exception(ocr.CallAborted())
                return future
            self.calls.append(fn.__name__)
            try:
                future.set_result(fn(*args))
            except Exception as exc:  # the call's own exception, as the worker returns it
                future.set_exception(exc)
        return future

    def count(self, name: str) -> int:
        return self.calls.count(name)


def grainy_photo(width: int, height: int) -> pymupdf.Pixmap:
    """Seeded random pixels: compresses as badly as a real photo, losslessly
    or not (the shared stripes compress to almost nothing losslessly)."""
    data = random.Random(7).randbytes(width * height * 3)
    return pymupdf.Pixmap(pymupdf.csRGB, width, height, data, 0)


class GatedWorker(InlineWorker):
    """An InlineWorker whose image pass waits at `gate` while it is closed,
    so a test can act while an estimate is mid-call."""

    def __init__(self):
        super().__init__()
        self.gate = threading.Event()
        self.waiting = threading.Event()

    def call(self, fn, args=(), timeout=0, abort=None) -> Future:
        if fn.__name__ == "compress_images" and not self.gate.is_set():
            self.waiting.set()
            self.gate.wait(WAIT_S)
        return super().call(fn, args, timeout, abort)


def tidy_photo_pdf(folder: Path) -> Path:
    """A 432 ppi photo in a PDF already tidied (garbage collected, streams
    compressed): the lossless level saves nothing, the image levels a lot."""
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_image(fx.PHOTO_BOX, pixmap=grainy_photo(*fx.PHOTO_PX))
    page.insert_text((50, 300), f"Page 1 {fx.KNOWN_WORD} caption", fontsize=12)
    doc.subset_fonts()
    path = folder / "photos.pdf"
    doc.save(path, garbage=4, deflate=True, clean=True)
    doc.close()
    return path


def text_scan_pixmap(width_pt: float, height_pt: float, ppi: int = 200) -> pymupdf.Pixmap:
    """A page of dark text on white, rendered as a colour picture."""
    source = pymupdf.open()
    page = source.new_page(width=width_pt, height=height_pt)
    for n in range(12):
        page.insert_text((40, 60 + n * 28), f"Line {n} of the {fx.KNOWN_WORD} survey report", fontsize=16)
    pix = page.get_pixmap(matrix=pymupdf.Matrix(ppi / 72, ppi / 72))
    source.close()
    return pix


def scan_with_photos_pdf(folder: Path, name: str = "scan.pdf") -> Path:
    """Page 1: a full-page colour scan with a small photo over a corner.
    Page 2: text and a small photo only."""
    doc = pymupdf.open()
    scan = doc.new_page()
    scan.insert_image(scan.rect, pixmap=text_scan_pixmap(scan.rect.width, scan.rect.height))
    scan.insert_image(pymupdf.Rect(400, 650, 560, 760), pixmap=fx._noisy_photo(600, 400))
    other = doc.new_page()
    other.insert_text((72, 100), "A page with a photo", fontsize=12)
    other.insert_image(fx.PHOTO_BOX, pixmap=fx._noisy_photo(*fx.PHOTO_PX))
    path = folder / name
    doc.save(path)
    doc.close()
    return path


def image_kinds(path: Path) -> list[tuple[float, int, int]]:
    """Every image drawn: (share of its page it covers, colour components,
    bits per component)."""
    doc = pymupdf.open(path)
    try:
        out = []
        for page in doc:
            for info in page.get_image_info(xrefs=True):
                share = abs(pymupdf.Rect(info["bbox"]) & page.rect) / abs(page.rect)
                pix = pymupdf.Pixmap(doc, info["xref"])
                out.append((round(share, 2), pix.n - pix.alpha, info["bpc"]))
        return out
    finally:
        doc.close()


class _Folder(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self._tmp.name)
        self._state = tempfile.TemporaryDirectory()
        self.manifest = Path(self._state.name) / "compress-scratch.json"
        self.scratch = ScratchFiles(self.manifest)

    def tearDown(self):
        engine._OPEN.clear()
        self._tmp.cleanup()
        self._state.cleanup()

    def scratch_files(self) -> list[str]:
        return sorted(n for n in os.listdir(self.folder) if n.startswith(PREFIX))


# ---------------------------------------------------------------------- #
# The plan, on its own                                                     #
# ---------------------------------------------------------------------- #


class PlanTests(unittest.TestCase):
    def test_the_ladder_runs_from_best_quality_down_to_about_50_ppi_at_quality_30(self):
        ladder = compress_plan.LADDER
        self.assertIsNone(ladder[0].ppi)
        self.assertEqual((ladder[-1].ppi, ladder[-1].quality), (50, 30))
        # Colour down to 72 ppi; black and white only at the low end, so a
        # limit a colour step meets is met in colour.
        first_bw = next(n for n, step in enumerate(ladder) if step.black_and_white)
        self.assertEqual(ladder[first_bw - 1].ppi, 72)
        self.assertTrue(all(step.black_and_white for step in ladder[first_bw:]))
        self.assertEqual(ladder[first_bw].scans, compress_plan.GREY)
        self.assertTrue(all(step.scans == compress_plan.BITONAL for step in ladder[first_bw + 1 :]))

    def test_the_quality_levels_are_steps_on_it(self):
        self.assertEqual(compress_plan.LEVEL_STEPS, {"light": 0, "balanced": 2, "strong": 4})
        self.assertEqual(compress_plan.LEVEL_RECIPES["balanced"], compress_plan.Recipe(150, 80))
        self.assertEqual(compress_plan.LEVEL_RECIPES["strong"], compress_plan.Recipe(96, 65))
        self.assertEqual(compress_plan.QUALITY_ORDER, (2, 4, 0))

    def test_checking_goes_smallest_first_then_the_lossless_step_then_up(self):
        self.assertEqual(compress_plan.CHECK_ORDER, (9, 0, 8, 7, 6, 5, 4, 3, 2, 1))
        self.assertEqual(sorted(compress_plan.CHECK_ORDER), list(range(len(compress_plan.LADDER))))

    def test_limits_count_1_kb_as_1000_bytes(self):
        self.assertEqual(compress_plan.limit_bytes(100, "KB"), 100_000)
        self.assertEqual(compress_plan.limit_bytes(1, "MB"), 1_000_000)
        for value, unit in ((0, "KB"), (10000, "KB"), (1.5, "MB"), ("100", "KB"), (True, "KB"), (100, "GB"), (5, ["KB"])):
            with self.subTest(value=value, unit=unit):
                self.assertIsNone(compress_plan.limit_bytes(value, unit))

    def test_a_step_fits_at_97_percent_of_the_limit_or_less(self):
        self.assertTrue(compress_plan.fits(97_000, 100_000))
        self.assertFalse(compress_plan.fits(97_001, 100_000))

    def test_the_presets(self):
        self.assertEqual(
            [f"{v} {u}" for v, u in compress_plan.PRESETS],
            ["50 KB", "100 KB", "200 KB", "300 KB", "500 KB", "1 MB", "2 MB"],
        )

    def test_a_limit_picks_the_best_step_at_97_percent_or_less(self):
        sizes = {n: size * 1000 for n, size in enumerate([900, 880, 550, 260, 147, 60, 57, 29, 11, 9])}
        for limit in range(10_000, 1_000_000, 7_919):
            with self.subTest(limit=limit):
                best, settled = compress_plan.choose(sizes, limit)
                fitting = [n for n, size in sizes.items() if size <= limit * 0.97]
                self.assertEqual(best, min(fitting) if fitting else None)
                self.assertTrue(settled)

    def test_a_choice_is_settled_once_the_step_above_is_known_not_to_fit(self):
        # Made smallest first: 9, then 0, then 8, 7, … up.
        self.assertEqual(compress_plan.choose({9: 9_000}, 100_000), (9, False))
        self.assertEqual(compress_plan.choose({9: 9_000, 0: 900_000, 8: 50_000}, 100_000), (8, False))
        self.assertEqual(compress_plan.choose({9: 9_000, 0: 900_000, 8: 50_000, 7: 120_000}, 100_000), (8, True))
        # A step that failed is skipped over: the next one up decides.
        self.assertEqual(compress_plan.choose({9: 9_000, 0: 900_000, 8: 50_000, 7: None}, 100_000), (8, False))
        self.assertEqual(compress_plan.choose({9: 9_000, 0: 900_000, 8: 50_000, 7: None, 6: 120_000}, 100_000), (8, True))

    def test_too_small_once_the_smallest_and_the_lossless_steps_are_known(self):
        self.assertEqual(compress_plan.choose({9: 200_000}, 100_000), (None, False))
        self.assertEqual(compress_plan.choose({9: 200_000, 0: 900_000}, 100_000), (None, True))
        # The smallest step failed: only every step settles it.
        self.assertEqual(compress_plan.choose({9: None, 0: 900_000}, 100_000), (None, False))
        self.assertEqual(compress_plan.choose({n: None for n in range(10)}, 100_000), (None, True))
        self.assertEqual(compress_plan.floor({9: 200_000, 0: 150_000, 3: None}), 150_000)

    def test_what_each_step_is_called(self):
        kind = compress_plan.kind
        ladder = compress_plan.LADDER
        changed = {"rewritten": True}
        self.assertEqual(kind(ladder[0], {}), compress_plan.SHARP)
        self.assertEqual(kind(ladder[3], changed), compress_plan.SHARP)  # 120 ppi
        self.assertEqual(kind(ladder[4], changed), compress_plan.GOOD)  # 96
        self.assertEqual(kind(ladder[5], changed), compress_plan.GOOD)  # 72
        self.assertEqual(kind(ladder[7], changed), compress_plan.READABLE)  # colour at 60, no scans
        self.assertEqual(kind(ladder[9], changed), compress_plan.READABLE)  # colour at 50
        self.assertEqual(kind(ladder[6], {**changed, "grey": 2}), compress_plan.READABLE)
        self.assertEqual(kind(ladder[8], {**changed, "grey": 1, "bitonal": 1}), compress_plan.BLACK_AND_WHITE)
        # Nothing resampled: as sharp as the original.
        self.assertEqual(kind(ladder[9], {"rewritten": False}), compress_plan.SHARP)
        self.assertEqual(
            [k for k in compress_plan.KINDS if compress_plan.needs_reading_check(k)],
            [compress_plan.READABLE, compress_plan.BLACK_AND_WHITE],
        )

    def test_steps_that_make_the_same_file(self):
        same = compress_plan.same_result
        self.assertEqual(same(9, {"images": False, "scans": 0}), list(range(9)))
        # No scanned page: a black-and-white step is the colour step at its settings.
        self.assertEqual(same(9, {"images": True, "scans": 0}), [8])
        self.assertEqual(same(6, {"images": True, "scans": 0}), [5])
        self.assertEqual(same(7, {"images": True, "scans": 0}), [])
        self.assertEqual(same(9, {"images": True, "scans": 3}), [])
        self.assertEqual(same(9, {}), [])


# ---------------------------------------------------------------------- #
# Black and white                                                          #
# ---------------------------------------------------------------------- #


class TextOnlyTests(_Folder):
    def test_a_pdf_with_no_images_is_never_made_bigger_by_an_image_step(self):
        source = self.folder / "text.pdf"
        doc = pymupdf.open()
        for n in range(40):
            doc.new_page().insert_text((72, 100), f"Page {n} of a report with only text", fontsize=12)
        doc.save(source)
        doc.close()
        sizes = []
        for n, recipe in enumerate(compress_plan.LADDER):
            tmp = self.folder / f"{n}.bin"
            attempt = tools.compress_attempt(fx.InlineContext(), tools.Source(str(source)), str(tmp), recipe)
            sizes.append(attempt.size)
            self.assertEqual(attempt.facts, {"images": False, "scans": 0})
            self.assertEqual(attempt.kind, compress_plan.SHARP)
        self.assertEqual(len(set(sizes)), 1, sizes)


class BlackAndWhiteTests(_Folder):
    def compress_with(self, source: Path, recipe: compress_plan.Recipe) -> tuple[Path, dict]:
        engine.open_job("bw", None, str(source), None)
        try:
            info = engine.compress_images("bw", recipe)
            out = self.folder / "out.pdf"
            engine.save_compressed("bw", str(out))
        finally:
            engine.close_job("bw")
        return out, info

    def split(self, out: Path):
        kinds = image_kinds(out)
        full_page = [(n, bpc) for share, n, bpc in kinds if share > 0.9]
        photos = [(n, bpc) for share, n, bpc in kinds if share < 0.2]
        return full_page, photos

    def test_grey_touches_only_the_full_page_scan(self):
        source = scan_with_photos_pdf(self.folder)
        out, info = self.compress_with(source, compress_plan.LADDER[6])
        full_page, photos = self.split(out)
        self.assertEqual(full_page, [(1, 8)])
        self.assertEqual(len(photos), 2)
        self.assertTrue(all(n == 3 for n, _bpc in photos))
        self.assertTrue(info["black_and_white"])
        self.assertEqual((info["grey"], info["bitonal"], info["scans"]), (1, 0, 1))
        self.assertEqual(compress_plan.kind(compress_plan.LADDER[6], info), compress_plan.READABLE)

    def test_one_bit_touches_only_the_full_page_scan(self):
        source = scan_with_photos_pdf(self.folder)
        out, info = self.compress_with(source, compress_plan.LADDER[7])
        full_page, photos = self.split(out)
        self.assertEqual(full_page, [(1, 1)])
        self.assertTrue(all(n == 3 for n, _bpc in photos))
        self.assertEqual((info["grey"], info["bitonal"]), (0, 1))
        self.assertEqual(compress_plan.kind(compress_plan.LADDER[7], info), compress_plan.BLACK_AND_WHITE)
        doc = pymupdf.open(out)
        # It still renders, black text on white: the corner is paper.
        pix = doc[0].get_pixmap(dpi=36, clip=pymupdf.Rect(0, 0, 30, 30))
        self.assertGreater(min(pix.samples), 200)
        doc.close()

    def test_colour_steps_keep_every_image_in_colour(self):
        source = scan_with_photos_pdf(self.folder)
        out, info = self.compress_with(source, compress_plan.LADDER[5])
        self.assertTrue(all(n == 3 for _share, n, _bpc in image_kinds(out)))
        self.assertFalse(info["black_and_white"])
        self.assertTrue(info["lowered"])
        self.assertTrue(info["rewritten"])
        self.assertEqual(compress_plan.kind(compress_plan.LADDER[5], info), compress_plan.GOOD)

    def test_a_page_with_only_small_photos_is_never_made_grey(self):
        out, info = self.compress_with(fx.photo_pdf(self.folder), compress_plan.LADDER[-1])
        self.assertTrue(all(n == 3 for _share, n, _bpc in image_kinds(out)))
        self.assertFalse(info["black_and_white"])
        self.assertEqual((info["images"], info["scans"]), (True, 0))
        self.assertEqual(compress_plan.kind(compress_plan.LADDER[-1], info), compress_plan.READABLE)


# ---------------------------------------------------------------------- #
# The ladder results, shared by both tabs                                  #
# ---------------------------------------------------------------------- #


class _Estimates(_Folder):
    def setUp(self):
        super().setUp()
        self.worker = InlineWorker()
        self.events: list[dict] = []
        self.estimates = CompressEstimates(self.worker, self.scratch, self.events.append)

    def tearDown(self):
        self.estimates.stop(discard=True)
        self.wait_idle()
        super().tearDown()

    def wait_idle(self):
        deadline = time.monotonic() + WAIT_S
        while self.estimates._thread is not None and time.monotonic() < deadline:
            time.sleep(0.05)

    def wait_done(self, session: int, steps) -> dict:
        deadline = time.monotonic() + WAIT_S
        while time.monotonic() < deadline:
            snap = self.estimates.snapshot(session)
            if all(snap.get(step, {}).get("state") == "done" for step in steps):
                return snap
            time.sleep(0.05)
        self.fail(f"never finished: {self.estimates.snapshot(session)}")

    def start(self, source: Path, mode: str = compress_plan.QUALITY, session=None) -> int:
        return self.estimates.start(tools.Source(str(source)), [str(self.folder)], session, mode)

    def made(self) -> list[int]:
        """The steps actually compressed, in order."""
        return [e["step"] for e in self.events if e["state"] == "working"]

    def run_job(self, build) -> dict:
        finished = threading.Event()
        outcome = {}

        def on_event(event):
            if event["state"] != jobs.WORKING:
                outcome.update(event)
                finished.set()

        runner = jobs.FileJobRunner(self.worker, on_event)
        runner.start("compress", build)
        self.assertTrue(finished.wait(WAIT_S))
        return outcome

    def run_quality(self, source: Path, level: str, session: int) -> dict:
        step = compress_plan.LEVEL_STEPS[level]
        dest = self.folder / "photos (compressed).pdf"
        self.estimates.give_way(session, step)
        return self.run_job(
            lambda ctx: tools.compress(
                ctx,
                tools.Source(str(source)),
                str(dest),
                level,
                self.scratch,
                lambda c: self.estimates.wait_for(session, step, c),
            )
        )

    def run_limit(self, source: Path, limit: int, session: int, held: list | None = None) -> dict:
        step, settled = self.estimates.choose(session, limit)
        self.assertTrue(settled)
        self.assertIsNotNone(step)
        dest = self.folder / "out.pdf"
        self.estimates.give_way(session, step)
        return self.run_job(
            lambda ctx: tools.compress_to_limit(
                ctx,
                tools.Source(str(source)),
                str(dest),
                step,
                limit,
                "X KB",
                self.scratch,
                lambda c: self.estimates.wait_for(session, step, c),
                (held if held is not None else []).append,
            )
        )


class QualityEstimateTests(_Estimates):
    def test_the_estimates_fill_in_and_no_change_follows_the_5_percent_rule(self):
        source = tidy_photo_pdf(self.folder)
        session = self.start(source)
        snap = self.wait_done(session, compress_plan.QUALITY_ORDER)
        before = os.path.getsize(source)
        for step, row in snap.items():
            with self.subTest(step=step):
                self.assertEqual(row["helps"], row["size"] <= before * 0.95)
        self.assertFalse(snap[0]["helps"])  # Best quality: "No change"
        self.assertTrue(snap[2]["helps"])
        self.assertTrue(snap[4]["helps"])
        self.assertLess(snap[4]["size"], snap[2]["size"])
        self.assertEqual(self.made(), list(compress_plan.QUALITY_ORDER))
        self.assertEqual(len(self.scratch_files()), 3)

    def test_compress_on_a_finished_estimate_does_no_second_compression(self):
        source = tidy_photo_pdf(self.folder)
        session = self.start(source)
        snap = self.wait_done(session, compress_plan.QUALITY_ORDER)
        self.assertEqual(self.worker.count("compress_images"), 3)
        outcome = self.run_quality(source, "strong", session)
        self.assertEqual(outcome["state"], jobs.DONE, outcome)
        self.assertEqual(self.worker.count("compress_images"), 3)
        self.assertEqual(self.worker.count("save_compressed"), 3)
        self.assertEqual(outcome["result"]["after"], snap[4]["size"])
        self.assertEqual(len(self.scratch_files()), 2)  # the other two, until the dialog closes
        self.estimates.stop(discard=True)
        self.assertEqual(self.scratch_files(), [])
        self.assertEqual(self.scratch.tracked(), [])

    def test_compress_waits_for_a_running_estimate_and_the_others_give_way(self):
        source = tidy_photo_pdf(self.folder)
        session = self.start(source)
        outcome = self.run_quality(source, "balanced", session)
        self.assertEqual(outcome["state"], jobs.DONE, outcome)
        self.wait_idle()
        self.assertEqual(self.worker.count("compress_images"), 1)
        self.assertEqual(self.scratch_files(), [])

    def test_a_level_that_doesnt_help_writes_nothing(self):
        source = tidy_photo_pdf(self.folder)
        session = self.start(source)
        self.wait_done(session, compress_plan.QUALITY_ORDER)
        outcome = self.run_quality(source, "light", session)
        self.assertEqual(outcome["state"], jobs.FAILED)
        self.assertEqual(outcome["error"], messages.message(messages.NOT_SMALLER, messages.COMPRESS))
        self.assertFalse((self.folder / "photos (compressed).pdf").exists())

    def test_closing_while_one_runs_leaves_nothing_behind(self):
        source = tidy_photo_pdf(self.folder)
        self.start(source)
        self.estimates.stop(discard=True)
        self.wait_idle()
        self.assertEqual(self.scratch_files(), [])
        self.assertEqual(self.scratch.tracked(), [])

    def test_another_file_job_stops_them(self):
        source = tidy_photo_pdf(self.folder)
        session = self.start(source)
        self.estimates.give_way()
        self.wait_idle()
        self.assertLessEqual(len(self.estimates.snapshot(session)), 1)
        self.assertLessEqual(self.worker.count("compress_images"), 1)


class SizeLimitCheckTests(_Estimates):
    def test_every_step_is_made_smallest_first_and_kept_across_tabs(self):
        source = scan_with_photos_pdf(self.folder)
        session = self.start(source)
        self.wait_done(session, compress_plan.QUALITY_ORDER)
        # Size limit: the steps Quality made aren't made again.
        self.assertEqual(self.start(source, compress_plan.SIZE_LIMIT, session), session)
        snap = self.wait_done(session, range(len(compress_plan.LADDER)))
        self.assertEqual(self.made(), [2, 4, 0, 9, 8, 7, 6, 5, 3, 1])
        self.assertEqual(self.worker.count("compress_images"), len(compress_plan.LADDER))
        # And back to Quality: nothing is redone.
        self.start(source, compress_plan.QUALITY, session)
        self.wait_idle()
        self.assertEqual(self.worker.count("compress_images"), len(compress_plan.LADDER))
        sizes = [snap[n]["size"] for n in range(len(compress_plan.LADDER))]
        self.assertEqual(sizes[1:], sorted(sizes[1:], reverse=True), sizes)
        kinds = [snap[n]["kind"] for n in range(len(compress_plan.LADDER))]
        self.assertEqual(kinds[6], compress_plan.READABLE)
        self.assertEqual(kinds[7:], [compress_plan.BLACK_AND_WHITE] * 3)
        self.assertEqual(len(self.scratch_files()), len(compress_plan.LADDER))
        self.estimates.stop(discard=True)
        self.assertEqual(self.scratch_files(), [])
        self.assertEqual(json.loads(self.manifest.read_text())["files"], [])

    def test_a_pdf_with_no_scanned_page_shares_the_black_and_white_steps(self):
        source = fx.photo_pdf(self.folder)
        session = self.start(source, compress_plan.SIZE_LIMIT)
        snap = self.wait_done(session, range(len(compress_plan.LADDER)))
        # 8 is 9 (both colour 50/30 here), 5 is 6 (both colour 72/50).
        self.assertNotIn(8, self.made())
        self.assertNotIn(5, self.made())
        self.assertEqual(len(self.made()), len(compress_plan.LADDER) - 2)
        self.assertEqual(snap[8]["size"], snap[9]["size"])
        self.assertEqual(snap[5]["size"], snap[6]["size"])
        self.assertEqual(len(self.scratch_files()), len(compress_plan.LADDER) - 2)
        self.estimates.stop(discard=True)
        self.assertEqual(self.scratch_files(), [])

    def test_a_pdf_with_no_image_is_checked_in_one_attempt(self):
        source = self.folder / "text.pdf"
        doc = pymupdf.open()
        for n in range(30):
            doc.new_page().insert_text((72, 100), f"Page {n}, text only", fontsize=12)
        doc.save(source)
        doc.close()
        session = self.start(source, compress_plan.SIZE_LIMIT)
        snap = self.wait_done(session, range(len(compress_plan.LADDER)))
        self.assertEqual(self.made(), [compress_plan.LAST_STEP])
        self.assertEqual(len({row["size"] for row in snap.values()}), 1)
        self.assertEqual(len(self.scratch_files()), 1)

    def test_compress_moves_the_steps_file_with_no_second_compression(self):
        source = scan_with_photos_pdf(self.folder)
        session = self.start(source, compress_plan.SIZE_LIMIT)
        snap = self.wait_done(session, range(len(compress_plan.LADDER)))
        sizes = {n: row["size"] for n, row in snap.items()}
        compressions = self.worker.count("compress_images")
        # A limit midway between two steps: the better one doesn't fit at 97%.
        limit = int((sizes[3] / 0.97 + sizes[2] / 0.97) / 2)
        outcome = self.run_limit(source, limit, session)
        self.assertEqual(outcome["state"], jobs.DONE, outcome)
        result = outcome["result"]
        self.assertEqual(result["after"], sizes[3])
        self.assertLessEqual(result["after"] * 100, limit * 97)
        self.assertEqual(result["kind"], snap[3]["kind"])
        self.assertEqual(self.worker.count("compress_images"), compressions)
        self.assertEqual(self.worker.count("save_compressed"), compressions)
        self.assertEqual(len(self.scratch_files()), len(compress_plan.LADDER) - 1)
        self.estimates.stop(discard=True)
        self.assertEqual(self.scratch_files(), [])
        self.assertEqual(self.scratch.tracked(), [])

    def test_a_shared_file_taken_by_a_job_takes_its_twin_too(self):
        source = fx.photo_pdf(self.folder)
        session = self.start(source, compress_plan.SIZE_LIMIT)
        snap = self.wait_done(session, range(len(compress_plan.LADDER)))
        # Between step 7 and the 8/9 pair: 8 is the best that fits.
        limit = int((snap[8]["size"] / 0.97 + snap[7]["size"] / 0.97) / 2)
        self.assertEqual(self.estimates.choose(session, limit), (8, True))
        outcome = self.run_limit(source, limit, session)
        self.assertEqual(outcome["state"], jobs.DONE, outcome)
        self.assertEqual(outcome["result"]["after"], snap[8]["size"])
        stopped = [e["step"] for e in self.events if e["state"] == "stopped"]
        self.assertEqual(sorted(stopped), [8, 9])
        left = self.estimates.snapshot(session)
        self.assertNotIn(8, left)
        self.assertNotIn(9, left)

    def test_the_safety_net_holds_a_step_that_doesnt_fit_after_all(self):
        source = scan_with_photos_pdf(self.folder)
        dest = self.folder / "out.pdf"
        tmp = self.scratch.new([str(self.folder)])
        attempt = tools.compress_attempt(fx.InlineContext(), tools.Source(str(source)), tmp, compress_plan.LADDER[9])
        held = []
        with self.assertLogs("lector.features.file_tools.tools", "WARNING"):
            result = tools.compress_to_limit(
                fx.InlineContext(), tools.Source(str(source)), str(dest), 9, attempt.size, "1 KB",
                self.scratch, lambda ctx: attempt, held.append,
            )
        self.assertTrue(result["unreachable"])
        self.assertFalse(dest.exists())
        self.assertEqual([h.path for h in held], [tmp])
        saved = tools.save_held(fx.InlineContext(), tools.Source(str(source)), str(dest), held[0], "1 KB", self.scratch)
        self.assertTrue(saved["over_limit"])
        self.assertEqual(os.path.getsize(dest), attempt.size)
        self.assertEqual(self.scratch_files(), [])

    def test_a_steps_file_gone_from_outside_is_made_again(self):
        source = scan_with_photos_pdf(self.folder)
        dest = self.folder / "out.pdf"
        tmp = self.scratch.new([str(self.folder)])
        attempt = tools.compress_attempt(fx.InlineContext(), tools.Source(str(source)), tmp, compress_plan.LADDER[9])
        os.remove(tmp)
        with self.assertLogs("lector.features.file_tools.tools", "WARNING"):
            result = tools.compress_to_limit(
                fx.InlineContext(), tools.Source(str(source)), str(dest), 9, attempt.size * 10, "X KB",
                self.scratch, lambda ctx: attempt, [].append,
            )
        self.assertEqual(result["after"], os.path.getsize(dest))
        self.assertEqual(result["kind"], compress_plan.BLACK_AND_WHITE)
        self.assertEqual(self.scratch_files(), [])
        self.assertEqual(self.scratch.tracked(), [])


    def test_a_level_whose_file_has_gone_is_made_again(self):
        source = tidy_photo_pdf(self.folder)
        session = self.start(source)
        snap = self.wait_done(session, compress_plan.QUALITY_ORDER)
        os.remove(self.estimates._results[4].path)  # an antivirus, say
        outcome = self.run_quality(source, "strong", session)
        self.assertEqual(outcome["state"], jobs.DONE, outcome)
        self.assertEqual(outcome["result"]["after"], snap[4]["size"])
        self.assertEqual(self.worker.count("compress_images"), 4)


class EstimateRaceTests(_Folder):
    def test_reopening_while_a_stopped_estimate_finishes_still_estimates_every_level(self):
        worker = GatedWorker()
        estimates = CompressEstimates(worker, self.scratch, lambda event: None)
        source = tools.Source(str(tidy_photo_pdf(self.folder)))
        try:
            estimates.start(source, [str(self.folder)])
            self.assertTrue(worker.waiting.wait(WAIT_S))  # Balanced is mid-call
            estimates.stop(discard=True)  # the dialog closed...
            session = estimates.start(source, [str(self.folder)])  # ...and opened again
            worker.gate.set()
            deadline = time.monotonic() + WAIT_S
            while time.monotonic() < deadline:
                snap = estimates.snapshot(session)
                if all(snap.get(step, {}).get("state") == "done" for step in compress_plan.QUALITY_ORDER):
                    break
                time.sleep(0.05)
            self.assertEqual(
                {step: row["state"] for step, row in estimates.snapshot(session).items()},
                {step: "done" for step in compress_plan.QUALITY_ORDER},
            )
            self.assertEqual(len(self.scratch_files()), 3)  # the stopped run's file went
        finally:
            worker.gate.set()
            estimates.stop(discard=True)
            estimates.wait(WAIT_S)

    def test_switching_to_quality_stops_a_size_limit_step_quality_doesnt_need(self):
        worker = GatedWorker()
        events = []
        estimates = CompressEstimates(worker, self.scratch, events.append)
        source = tools.Source(str(tidy_photo_pdf(self.folder)))
        try:
            session = estimates.start(source, [str(self.folder)], None, compress_plan.SIZE_LIMIT)
            self.assertTrue(worker.waiting.wait(WAIT_S))  # step 9 is mid-call
            estimates.start(source, [str(self.folder)], session, compress_plan.QUALITY)
            worker.gate.set()
            deadline = time.monotonic() + WAIT_S
            while time.monotonic() < deadline and estimates._thread is not None:
                time.sleep(0.05)
            snap = estimates.snapshot(session)
            self.assertEqual(sorted(snap), sorted(compress_plan.QUALITY_ORDER))
            self.assertIn({"session": session, "step": 9, "state": "stopped"}, events)
        finally:
            worker.gate.set()
            estimates.stop(discard=True)
            estimates.wait(WAIT_S)

    def test_a_held_result_survives_a_save_that_never_starts(self):
        estimates = CompressEstimates(InlineWorker(), self.scratch, lambda event: None)
        source = tools.Source(str(tidy_photo_pdf(self.folder)))
        session = estimates.start(source, [str(self.folder)])
        estimates.stop(discard=False)
        estimates.wait(WAIT_S)
        path = self.scratch.new([str(self.folder)])
        Path(path).write_bytes(b"%PDF-1.7 smallest")
        estimates.hold(session, tools.Attempt(path, 17, {}, False, False))
        self.assertTrue(estimates.has_held(session))  # what the request checks
        self.assertTrue(estimates.has_held(session))  # still there for the next one
        self.assertEqual(estimates.take_held(session).path, path)  # taken by the job
        self.assertFalse(estimates.has_held(session))
        estimates.stop(discard=True)
        estimates.wait(WAIT_S)


class LeftoverTests(_Folder):
    @unittest.skipUnless(os.name == "nt", "Windows refuses to delete a file another handle has open")
    def test_a_file_that_cant_be_deleted_yet_stays_listed_for_the_next_start(self):
        path = self.scratch.new([str(self.folder)])
        with open(path, "rb"):
            self.scratch.discard_all()
            self.assertEqual(self.scratch.tracked(), [path])
            self.assertIn(path, json.loads(self.manifest.read_text())["files"])
        self.assertEqual(clean_leftovers(self.manifest), 1)
        self.assertFalse(os.path.exists(path))

    def test_leftovers_from_an_earlier_run_go_at_the_next_start(self):
        ours = self.scratch.new([str(self.folder)])
        theirs = self.folder / "report.tmp"
        theirs.write_bytes(b"keep me")
        manifest = json.loads(self.manifest.read_text())
        manifest["files"].append(str(theirs))
        self.manifest.write_text(json.dumps(manifest))
        # The app was killed: nothing cleaned up. The next start:
        self.assertEqual(clean_leftovers(self.manifest), 1)
        self.assertFalse(os.path.exists(ours))
        self.assertTrue(theirs.exists())
        self.assertFalse(self.manifest.exists())

    def test_a_missing_or_damaged_manifest_is_harmless(self):
        self.assertEqual(clean_leftovers(self.manifest), 0)
        self.manifest.write_text("{not json")
        self.assertEqual(clean_leftovers(self.manifest), 0)


# ---------------------------------------------------------------------- #
# The bridge                                                               #
# ---------------------------------------------------------------------- #


class ApiCompressTests(_Folder):
    def setUp(self):
        super().setUp()
        self._settings = mock.patch.object(store, "_settings_path", lambda: Path(self._state.name) / "settings.json")
        self._settings.start()
        self.window = mock.Mock()
        self._windows = mock.patch.object(api_module.webview, "windows", [self.window])
        self._windows.start()
        self.api = api_module.Api()
        self.api._voice = mock.MagicMock()

    def tearDown(self):
        self.api._jobs.cancel()
        self.api._jobs.wait(WAIT_S)
        self.api.shutdown_ocr()
        self._windows.stop()
        self._settings.stop()
        super().tearDown()

    def options(self, **extra) -> dict:
        return {"folder": str(self.folder), "name": "out", **extra}

    def job_ends(self) -> list[dict]:
        ends = []
        for call in self.window.evaluate_js.call_args_list:
            script = call.args[0]
            if "lector:filejob" in script:
                event = json.loads(script.split("detail: ", 1)[1].rsplit("}))", 1)[0])
                if event["state"] != jobs.WORKING:
                    ends.append(event)
        return ends

    def wait_end(self, count: int) -> dict:
        deadline = time.monotonic() + WAIT_S
        while time.monotonic() < deadline:
            ends = self.job_ends()
            if len(ends) >= count:
                return ends[count - 1]
            time.sleep(0.1)
        self.fail("the job never ended")

    def checked(self, source: Path) -> tuple[int, dict]:
        """Open Size limit for `source` and wait for every step."""
        session = self.api.start_compress_estimates(str(source), str(self.folder), None, "limit")["session"]
        deadline = time.monotonic() + WAIT_S
        while time.monotonic() < deadline:
            snap = self.api.get_compress_estimates(session)
            if len(snap) == len(compress_plan.LADDER) and all(r["state"] == "done" for r in snap.values()):
                return session, snap
            time.sleep(0.1)
        self.fail(f"the checking never finished: {self.api.get_compress_estimates(session)}")

    def test_a_reachable_limit_is_written_from_the_checked_step(self):
        source = scan_with_photos_pdf(self.folder)
        session, snap = self.checked(source)
        limit_kb = snap[9]["size"] // 970 + 1
        options = self.options(mode="limit", limit=limit_kb, unit="KB", session=session)
        answer = self.api.start_file_job("compress", str(source), options)
        self.assertTrue(answer["ok"], answer)
        end = self.wait_end(1)
        self.assertEqual(end["state"], jobs.DONE, end)
        step, _ = compress_plan.choose({n: r["size"] for n, r in snap.items()}, limit_kb * 1000)
        self.assertEqual(end["result"]["after"], snap[step]["size"])
        self.assertEqual(end["result"]["kind"], snap[step]["kind"])
        self.assertEqual(store.get_file_tool_prefs()["compress_limit"], limit_kb)
        self.api.stop_compress_estimates(True)
        self.assertEqual(self.scratch_files(), [])

    def test_a_protected_pdf_from_home_compresses_to_a_limit(self):
        plain = scan_with_photos_pdf(self.folder, name="plain.pdf")
        locked = self.folder / "locked.pdf"
        doc = pymupdf.open(plain)
        doc.save(locked, encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw=pdf_support.PASSWORD, owner_pw="owner")
        doc.close()
        self.assertTrue(self.api.file_tool_info("compress", str(locked), pdf_support.PASSWORD)["ok"])
        session, snap = self.checked(locked)
        limit_kb = snap[9]["size"] // 970 + 1
        options = self.options(mode="limit", limit=limit_kb, unit="KB", session=session)
        self.assertTrue(self.api.start_file_job("compress", str(locked), options)["ok"])
        end = self.wait_end(1)
        self.assertEqual(end["state"], jobs.DONE, end)
        out = pymupdf.open(self.folder / "out.pdf")
        self.assertTrue(out.needs_pass)
        self.assertTrue(out.authenticate(pdf_support.PASSWORD))
        out.close()

    def test_a_limit_the_checking_hasnt_settled_or_cant_reach_is_refused(self):
        source = scan_with_photos_pdf(self.folder)
        # No session's results at all.
        self.assertFalse(self.api.start_file_job("compress", str(source), self.options(mode="limit", limit=1, unit="KB"))["ok"])
        session, snap = self.checked(source)
        too_small = max(1, snap[9]["size"] // 1000 - 1)
        options = self.options(mode="limit", limit=too_small, unit="KB", session=session)
        self.assertFalse(self.api.start_file_job("compress", str(source), options)["ok"])
        self.assertFalse((self.folder / "out.pdf").exists())

    def test_a_limit_at_or_above_the_files_size_is_refused(self):
        source = scan_with_photos_pdf(self.folder)
        session, _ = self.checked(source)
        limit_kb = os.path.getsize(source) // 1000 + 1
        options = self.options(mode="limit", limit=limit_kb, unit="KB", session=session)
        self.assertFalse(self.api.start_file_job("compress", str(source), options)["ok"])

    def test_a_compress_job_that_cant_start_leaves_the_estimates_running(self):
        source = tidy_photo_pdf(self.folder)
        session = self.api.start_compress_estimates(str(source), str(self.folder))["session"]
        with mock.patch.object(self.api._jobs, "start", return_value=None):
            options = self.options(mode="quality", level="strong", session=session)
            self.assertTrue(self.api.start_file_job("compress", str(source), options)["busy"])
        deadline = time.monotonic() + WAIT_S
        while time.monotonic() < deadline:
            snap = self.api.get_compress_estimates(session)
            if len(snap) == 3 and all(r["state"] == "done" for r in snap.values()):
                break
            time.sleep(0.1)
        self.assertEqual(sorted(self.api.get_compress_estimates(session)), sorted(compress_plan.QUALITY_ORDER))

    def test_bad_limits_are_refused(self):
        source = fx.photo_pdf(self.folder)
        for limit, unit in ((0, "KB"), (10000, "KB"), ("5", "KB"), (5, "GB"), (None, None), (5, ["KB"]), (5, {"KB": 1})):
            with self.subTest(limit=limit, unit=unit):
                options = self.options(mode="limit", limit=limit, unit=unit)
                self.assertFalse(self.api.start_file_job("compress", str(source), options)["ok"])
        self.assertFalse(self.api.start_compress_estimates(str(source), str(self.folder), None, "smallest")["ok"])

    def test_saving_the_smallest_needs_a_held_result(self):
        source = fx.photo_pdf(self.folder)
        session = self.api.start_compress_estimates(str(source), str(self.folder))["session"]
        options = self.options(mode="save_held", limit=1, unit="KB", session=session)
        self.assertFalse(self.api.start_file_job("compress", str(source), options)["ok"])

    def test_the_mode_and_limit_are_remembered(self):
        prefs = store.get_file_tool_prefs()
        self.assertEqual((prefs["compress_mode"], prefs["compress_limit"], prefs["compress_unit"]), ("quality", 200, "KB"))
        self.assertTrue(self.api.set_compress_mode("limit")["ok"])
        self.assertFalse(self.api.set_compress_mode("smallest")["ok"])
        self.assertEqual(store.get_file_tool_prefs()["compress_mode"], "limit")

    def test_quitting_deletes_every_temporary_file(self):
        source = tidy_photo_pdf(self.folder)
        self.api.start_compress_estimates(str(source), str(self.folder), None, "limit")
        self.api._scratch.new([str(self.folder)])
        self.api.shutdown_ocr()
        self.assertEqual(self.scratch_files(), [])


# ---------------------------------------------------------------------- #
# Names                                                                    #
# ---------------------------------------------------------------------- #


class SuffixTests(_Folder):
    def test_no_tool_stacks_its_own_suffix(self):
        cases = {
            messages.COMPRESS: "compressed",
            messages.SEARCHABLE: "searchable",
            messages.EXPORT: "images",
            messages.COMBINE: "combined",
            messages.SPLIT: "split",
        }
        for tool, label in cases.items():
            with self.subTest(tool=tool):
                once = destinations.base_stem(tool, str(self.folder / "Report.pdf"))
                self.assertEqual(once, f"Report ({label})")
                again = destinations.base_stem(tool, str(self.folder / f"{once}.pdf"))
                self.assertEqual(again, once)

    def test_a_clash_takes_the_next_free_number(self):
        (self.folder / "sample-scanned (compressed).pdf").write_bytes(b"%PDF-1.4")
        source = str(self.folder / "sample-scanned (compressed).pdf")
        self.assertEqual(
            destinations.suggested_stem(messages.COMPRESS, source, str(self.folder)), "sample-scanned (compressed) (2)"
        )

    def test_another_tools_suffix_is_still_added(self):
        source = str(self.folder / "Report (searchable).pdf")
        self.assertEqual(destinations.base_stem(messages.COMPRESS, source), "Report (searchable) (compressed)")

    def test_combine_from_a_combined_pdf(self):
        first = str(self.folder / "Report (combined).pdf")
        self.assertEqual(destinations.list_stem(messages.COMBINE, [first, "b.pdf"]), "Report (combined)")

    def test_extract_from_an_extract(self):
        for stem in ("Report (pages 4–6)", "Report (page 2)", "Report (5 pages)"):
            with self.subTest(stem=stem):
                self.assertEqual(destinations.extract_stem(str(self.folder / f"{stem}.pdf"), [1, 2], str(self.folder)), stem)
        self.assertEqual(
            destinations.extract_stem(str(self.folder / "Report.pdf"), [1, 2], str(self.folder)), "Report (pages 1–2)"
        )


if __name__ == "__main__":
    unittest.main()
