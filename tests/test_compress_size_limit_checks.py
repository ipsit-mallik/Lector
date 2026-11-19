"""Size limit's advance checking (docs/PRD.md § "Post-v1: file tools" →
Compress; docs/ARCHITECTURE.md § "File tools").

Held here:
- the stopping rule: steps run only while a visible pill's answer can still
  change, hidden pills don't count, and a typed limit does;
- a big file's sampled estimates and their margins: a pill is enabled on an
  estimate only at 80% of its limit or less, dimmed only at 130% or more,
  and "Checking…" in between; the scaling from a sample;
- the order of the real steps after sampling: the selected pill's step
  first, then the pills nearest a margin; choosing another pill re-orders;
- unused temporary files are deleted at once;
- Compress on a step that turns out over the limit takes the next one down
  that really fits, and says how;
- too little free space: files aren't kept, or the dialog is told there is
  no room, and asking again retries;
- every event carries a sequence number that only goes up;
- small files: no sample, and the same order as before.

The page's side of these (its copy of the decision, the cards, the stale
result race) is in tests/test_compress_panel_behavior.py.

The engine runs in this process through `InlineWorker` (tests/
test_compress_modes.py), which serializes calls as the real worker does.
"""
import os
import random
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pymupdf  # noqa: E402

import file_tools_support as fx  # noqa: E402
from lector.features.file_tools import compress_plan, limit_plan, sampling, scratch, tools  # noqa: E402
from lector.features.file_tools.estimates import CompressEstimates  # noqa: E402
from lector.features.file_tools.scratch import PREFIX, ScratchFiles  # noqa: E402
from lector.features.reading import safe_write  # noqa: E402
from test_compress_modes import InlineWorker, scan_with_photos_pdf  # noqa: E402

WAIT_S = 120
KB = compress_plan.KB
MB = compress_plan.MB

# The same step made twice can differ by a byte or two (MuPDF's output isn't
# byte-for-byte repeatable).
SAME_FILE_BYTES = 64

# The 10-page scan's real ladder (bytes, best quality first), and its size.
SCAN10 = [900983, 900961, 551793, 262308, 147254, 60038, 57710, 29255, 10739, 9389]
SCAN10_FILE = 901188


def sizes(values) -> dict:
    return dict(enumerate(values))


def run_plan(real_sizes: dict, limits: list[int], known: dict | None = None, estimates=None, selected=None) -> list[int]:
    """The steps `limit_plan.next_step` runs, in order, revealing each real
    size as it is made."""
    known = dict(known or {})
    made = []
    while (step := limit_plan.next_step(known, estimates, limits, selected)) is not None:
        made.append(step)
        known[step] = real_sizes[step]
        if len(made) > len(compress_plan.LADDER):
            raise AssertionError("never stopped")
    return made


# ---------------------------------------------------------------------- #
# The plan, on its own                                                     #
# ---------------------------------------------------------------------- #


class StoppingRuleTests(unittest.TestCase):
    def test_steps_stop_once_every_visible_pill_is_settled(self):
        limits = limit_plan.limits_of_interest(SCAN10_FILE, None)
        self.assertEqual(limits, [50 * KB, 100 * KB, 200 * KB, 300 * KB, 500 * KB])  # 1 and 2 MB hidden
        made = run_plan(sizes(SCAN10), limits)
        # Smallest first, as before; step 1 can't change any of these answers.
        self.assertEqual(made, [9, 0, 8, 7, 6, 5, 4, 3, 2])
        known = {step: SCAN10[step] for step in made}
        for limit in limits:
            with self.subTest(limit=limit):
                self.assertEqual(compress_plan.choose(known, limit), compress_plan.choose(sizes(SCAN10), limit))
                self.assertTrue(compress_plan.choose(known, limit)[1])

    def test_hidden_pills_dont_count(self):
        # A 250 KB file: only 50, 100 and 200 KB are visible.
        limits = limit_plan.limits_of_interest(250 * KB, None)
        self.assertEqual(limits, [50 * KB, 100 * KB, 200 * KB])
        ladder = [240_000, 239_000, 200_000, 150_000, 120_000, 60_000, 58_000, 30_000, 11_000, 9_000]
        made = run_plan(sizes(ladder), limits)
        self.assertNotIn(1, made)
        self.assertEqual(run_plan(sizes(ladder), []), [])

    def test_a_typed_limit_counts_as_a_pill(self):
        limits = limit_plan.limits_of_interest(SCAN10_FILE, 700 * KB)
        self.assertIn(700 * KB, limits)
        made = run_plan(sizes(SCAN10), limits)
        self.assertIn(1, made)  # 700 KB: step 2 fits, so whether 1 does matters

    def test_a_step_that_cant_change_an_answer_is_never_run(self):
        # Everything fits at step 0: it settles every pill, and nothing else
        # is needed.
        ladder = [20_000] * 10
        self.assertEqual(run_plan(sizes(ladder), limit_plan.limits_of_interest(SCAN10_FILE, None)), [9, 0])


class MarginTests(unittest.TestCase):
    """A big file's estimates decide only with room to spare."""

    LIMIT = 1 * MB

    def decide(self, estimate: int, real: dict | None = None) -> tuple:
        estimates = {step: estimate for step in range(10)}
        return limit_plan.decide(real or {}, estimates, self.LIMIT)

    def test_enabled_at_80_percent_or_less(self):
        self.assertEqual(self.decide(800_000), (0, True))
        self.assertEqual(self.decide(800_001), (None, False))  # Checking…

    def test_dimmed_at_130_percent_or_more(self):
        self.assertEqual(self.decide(1_300_000), (None, True))
        self.assertEqual(self.decide(1_299_999), (None, False))  # Checking…

    def test_between_the_margins_waits_for_a_real_step(self):
        for estimate in (850_000, 970_000, 1_000_000, 1_200_000):
            with self.subTest(estimate=estimate):
                self.assertEqual(self.decide(estimate), (None, False))

    def test_the_chosen_step_is_the_best_by_estimate(self):
        estimates = sizes([5_000_000, 4_000_000, 3_000_000, 1_500_000, 1_400_000, 700_000, 600_000, 300_000, 100_000, 90_000])
        # 1 MB: step 5 (700 KB, 70%) is the best that fits at 97%, with room;
        # step 4 above it is at 140%, ruled out.
        self.assertEqual(limit_plan.decide({}, estimates, 1 * MB), (5, True))
        # 2 MB: step 3 (1.5 MB, 75%); step 2 at 150%.
        self.assertEqual(limit_plan.decide({}, estimates, 2 * MB), (3, True))
        # 1.45 MB: step 4 (1.4 MB, 97%) is the best, with no room: Checking.
        self.assertEqual(limit_plan.decide({}, estimates, 1_450_000), (None, False))

    def test_real_results_override_estimates(self):
        estimates = sizes([5_000_000] * 5 + [700_000] * 5)
        # Step 5 really came out over 1 MB: the next step down by estimate.
        self.assertEqual(limit_plan.decide({5: 1_100_000}, estimates, 1 * MB), (6, True))
        # A real step that fits decides, wherever its estimate was.
        self.assertEqual(limit_plan.decide({4: 900_000, 3: 1_200_000}, estimates, 1 * MB), (4, True))
        # The floor really over the limit, the lossless step estimated far over.
        self.assertEqual(limit_plan.decide({9: 1_050_000}, {0: 9_000_000, 9: 1_000_000}, 1 * MB), (None, True))

    # Steps 1–3 estimated just over 1 MB (98–105%): too close to rule out.
    CLOSE = sizes([3_000_000, 1_050_000, 1_020_000, 980_000, 700_000, 600_000, 500_000, 400_000, 300_000, 100_000])

    def test_a_better_step_estimated_just_over_isnt_ruled_out(self):
        # Step 4 really fits, but step 3 might too: the limit is reachable,
        # not final, and step 3 is made next.
        real = {4: 700_000, 9: 100_000, 0: 3_000_000}
        self.assertEqual(limit_plan.decide(real, self.CLOSE, 1 * MB), (4, False))
        self.assertEqual(limit_plan.next_step(real, self.CLOSE, [1 * MB], 1 * MB), 3)
        # Step 3 really fits: now step 2 (102%) is the one in doubt.
        self.assertEqual(limit_plan.decide({**real, 3: 955_000}, self.CLOSE, 1 * MB), (3, False))
        # Step 2 really over: step 3 is final.
        self.assertEqual(limit_plan.decide({**real, 3: 955_000, 2: 1_030_000}, self.CLOSE, 1 * MB), (3, True))

    def test_a_better_step_the_sample_couldnt_make_isnt_ruled_out(self):
        estimates = {**self.CLOSE, 1: 9_000_000, 2: 9_000_000, 3: None}
        self.assertEqual(limit_plan.decide({4: 700_000}, estimates, 1 * MB), (4, False))
        self.assertEqual(limit_plan.next_step({4: 700_000}, estimates, [1 * MB], 1 * MB), 3)

    def test_a_better_step_estimated_far_over_is_ruled_out(self):
        estimates = {**self.CLOSE, 1: 9_000_000, 2: 9_000_000, 3: 1_300_000}
        self.assertEqual(limit_plan.decide({4: 700_000}, estimates, 1 * MB), (4, True))
        # On estimates alone too: step 4 at 70% with room, step 3 at 130%.
        self.assertEqual(limit_plan.decide({}, estimates, 1 * MB), (4, True))

    def test_a_step_with_room_but_a_better_one_in_doubt_is_reachable_not_final(self):
        # The pill can be enabled (step 4 at 70% fits with room), but its
        # card waits for step 3.
        self.assertEqual(limit_plan.decide({}, self.CLOSE, 1 * MB), (4, False))

    def test_partial_estimates_decide_nothing_early(self):
        # Only the smallest step estimated: no step's answer is known yet.
        self.assertEqual(limit_plan.decide({}, {9: 100_000}, 1 * MB), (None, False))
        # The smallest and lossless steps, both far over: too small already.
        self.assertEqual(limit_plan.decide({}, {9: 2_000_000, 0: 9_000_000}, 1 * MB), (None, True))


class RealStepOrderTests(unittest.TestCase):
    # A 110 MB scan's ladder, roughly.
    LADDER = [110_000_000, 110_000_000, 58_000_000, 28_000_000, 14_400_000, 5_080_000, 4_840_000, 2_280_000, 1_047_000, 1_047_000]

    def setUp(self):
        self.estimates = {step: round(size * 0.98) for step, size in enumerate(self.LADDER)}
        self.limits = limit_plan.limits_of_interest(110 * MB, None)

    def test_the_selected_pills_step_runs_first(self):
        # 2 MB is reachable on its estimate (step 8, about 51%), but step 7
        # (112%) might fit too: that is made first, then step 8 to confirm.
        self.assertEqual(limit_plan.decide({}, self.estimates, 2 * MB), (8, False))
        self.assertEqual(limit_plan.next_step({}, self.estimates, self.limits, 2 * MB), 7)
        real = {7: self.LADDER[7]}
        self.assertEqual(limit_plan.decide(real, self.estimates, 2 * MB), (8, True))
        self.assertEqual(limit_plan.next_step(real, self.estimates, self.limits, 2 * MB), 8)

    def test_a_pill_decided_on_its_estimate_runs_its_step_first(self):
        estimates = {**self.estimates, 7: 2_700_000}  # 135% of 2 MB: ruled out
        self.assertEqual(limit_plan.decide({}, estimates, 2 * MB), (8, True))
        self.assertEqual(limit_plan.next_step({}, estimates, self.limits, 2 * MB), 8)

    def test_without_a_selection_the_open_pill_goes_first(self):
        # Open: 1 MB (1.03 MB estimated: 103%; what decides it is the floor)
        # and 2 MB (step 7 in doubt). 1 MB is nearer a margin.
        open_limits = [limit for limit in self.limits if not limit_plan.decide({}, self.estimates, limit)[1]]
        self.assertEqual(open_limits, [1 * MB, 2 * MB])
        self.assertEqual(limit_plan.next_step({}, self.estimates, self.limits, None), 9)

    def test_the_pill_nearest_a_margin_goes_first(self):
        # Two open pills: 1 MB at 85% of its limit, 2 MB at 120%.
        estimates = sizes([9_000_000] * 7 + [1_900_000, 850_000, 850_000])
        limits = [1 * MB, 2 * MB]
        self.assertEqual([limit_plan.decide({}, estimates, limit)[1] for limit in limits], [False, False])
        # 1 MB's step 8 is at 85%, 5 points from its margin; 2 MB's step 7 at
        # 95%, 15 points: 1 MB's step goes first, then 2 MB's.
        self.assertEqual(limit_plan.next_step({}, estimates, limits, None), 8)
        self.assertEqual(limit_plan.next_step({8: 840_000}, estimates, limits, None), 7)

    def test_selecting_another_pill_reorders(self):
        # A third of the ladder: each pill's better steps are well over it.
        estimates = {step: round(size * 0.3) for step, size in enumerate(self.LADDER)}
        first = limit_plan.next_step({}, estimates, self.limits, 2 * MB)
        second = limit_plan.next_step({}, estimates, self.limits, 1 * MB)
        self.assertEqual(first, limit_plan.decide({}, estimates, 2 * MB)[0])
        self.assertEqual(second, limit_plan.decide({}, estimates, 1 * MB)[0])
        self.assertNotEqual(first, second)

    def test_it_stops_once_every_pill_is_decided(self):
        made = run_plan(sizes(self.LADDER), self.limits, estimates=self.estimates, selected=2 * MB)
        self.assertEqual(made[:2], [7, 8])  # the step in doubt, then the selected pill's
        self.assertLessEqual(len(made), 4)
        real = {s: self.LADDER[s] for s in made}
        for limit in self.limits:
            self.assertTrue(limit_plan.decide(real, self.estimates, limit)[1])

    def test_the_answers_are_what_is_kept(self):
        real = {9: 1_047_000, 8: 1_047_000, 0: 110_000_000}
        self.assertEqual(limit_plan.answers(real, self.estimates, self.limits), {8})


class ScalingTests(unittest.TestCase):
    def test_a_uniform_scan_scales_by_its_image_bytes(self):
        look = {"source_bytes": 100_000_000, "image_bytes": 99_000_000, "heavy_full": 4_000_000,
                "heavy_in": 4_000_000, "spread_in": 3_500_000, "sample_bytes": 7_520_000}
        measured = {"out": 760_000, "heavy_out": 400_000, "spread_out": 350_000}
        # Every image to a tenth, the 1 MB of everything else about the same.
        self.assertAlmostEqual(limit_plan.scale_sample(look, measured) / 10_900_000, 1, delta=0.05)

    def test_a_few_heavy_pages_dont_inflate_the_rest(self):
        # One 15 MB photo page and 99 text pages of 50 KB: the photo is in the
        # sample as a heavy page, two text pages as spread ones.
        look = {"source_bytes": 19_950_000, "image_bytes": 15_000_000, "heavy_full": 15_000_000,
                "heavy_in": 15_000_000, "spread_in": 0, "sample_bytes": 15_100_000}
        measured = {"out": 1_590_000, "heavy_out": 1_500_000, "spread_out": 0}
        truth = 1_500_000 + 99 * 45_000
        self.assertAlmostEqual(limit_plan.scale_sample(look, measured) / truth, 1, delta=0.1)

    def test_big_means_over_20_mb_or_over_30_pages(self):
        self.assertFalse(limit_plan.needs_sample(20 * MB, 30))
        self.assertTrue(limit_plan.needs_sample(20 * MB + 1, 1))
        self.assertTrue(limit_plan.needs_sample(1000, 31))

    def test_the_sample_takes_the_heaviest_pages_and_spreads_the_rest(self):
        per_page = [10, 10, 900, 10, 10, 10, 800, 10, 10, 10, 10, 10]
        heavy, spread = sampling.pick_pages(per_page, 2, 2)
        self.assertEqual(heavy, [2, 6])
        self.assertEqual(len(spread), 2)
        self.assertTrue(set(spread).isdisjoint(heavy))
        self.assertLess(spread[0], 6)
        self.assertGreater(spread[1], 6)


# ---------------------------------------------------------------------- #
# Through CompressEstimates                                                #
# ---------------------------------------------------------------------- #


def scan_page_pixmap(n: int, ppi: int = 100) -> pymupdf.Pixmap:
    """Page `n` as a colour scan: its own text over seeded grain, so no two
    pages' images are the same object and each weighs about as a real
    scan does."""
    rng = random.Random(n)
    source = pymupdf.open()
    page = source.new_page()
    for line in range(14):
        page.insert_text((40, 60 + line * 26), f"Page {n} line {line} of the survey {rng.random():.6f}", fontsize=14)
    pix = page.get_pixmap(matrix=pymupdf.Matrix(ppi / 72, ppi / 72))
    source.close()
    grain = bytearray(pix.samples)
    for i in range(0, len(grain), 3):
        grain[i] = max(0, grain[i] - rng.randrange(40))
    return pymupdf.Pixmap(pymupdf.csRGB, pix.width, pix.height, bytes(grain), 0)


def scan_pages_pdf(folder: Path, pages: int, name: str = "scans.pdf") -> Path:
    """`pages` full-page colour scans of text, each its own image."""
    doc = pymupdf.open()
    for n in range(pages):
        page = doc.new_page()
        page.insert_image(page.rect, pixmap=scan_page_pixmap(n))
    path = folder / name
    doc.save(path, garbage=4, deflate=True)
    doc.close()
    return path


class _Estimates(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self._tmp.name)
        self._state = tempfile.TemporaryDirectory()
        self.scratch = ScratchFiles(Path(self._state.name) / "compress-scratch.json")
        self.worker = InlineWorker()
        self.events: list[dict] = []
        self.estimates = CompressEstimates(self.worker, self.scratch, self.events.append)

    def tearDown(self):
        self.estimates.stop(discard=True)
        self.wait_idle()
        self._tmp.cleanup()
        self._state.cleanup()

    def wait_idle(self):
        deadline = time.monotonic() + WAIT_S
        while self.estimates._thread is not None and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertIsNone(self.estimates._thread)

    def start(self, source: Path, mode: str = compress_plan.SIZE_LIMIT, session=None, folders=None) -> int:
        return self.estimates.start(tools.Source(str(source)), folders or [str(self.folder)], session, mode)

    def made(self) -> list[int]:
        return [e["step"] for e in self.events if e.get("state") == "working"]

    def scratch_files(self, folder: Path | None = None) -> list[str]:
        return sorted(n for n in os.listdir(folder or self.folder) if n.startswith(PREFIX))


@mock.patch.object(limit_plan, "SAMPLE_MIN_PAGES", 5)
class SampledEstimateTests(_Estimates):
    """A "big" file: here, over 5 pages (the real threshold is 30 pages or
    20 MB; lowered so the test file stays small)."""

    def test_every_step_is_estimated_before_any_real_step_and_the_selected_one_runs_first(self):
        source = scan_pages_pdf(self.folder, 12)
        session = self.start(source)
        self.estimates.select(session, 200 * KB)
        self.wait_idle()
        estimate_events = [n for n, e in enumerate(self.events) if "estimates" in e]
        working = [n for n, e in enumerate(self.events) if e.get("state") == "working"]
        self.assertEqual(len(estimate_events), len(compress_plan.LADDER))
        self.assertTrue(working)
        self.assertLess(max(estimate_events), min(working))
        snap = self.estimates.snapshot(session)
        estimated = {step: row["size"] for step, row in snap["estimates"].items()}
        self.assertEqual(self.made()[0], limit_plan.next_step({}, estimated, self.limits(source, 200 * KB), 200 * KB))
        # It stopped with every visible pill decided, before every step.
        real = {s: row["size"] for s, row in snap["steps"].items()}
        for limit in self.limits(source, 200 * KB):
            with self.subTest(limit=limit):
                self.assertTrue(limit_plan.decide(real, estimated, limit)[1])
        self.assertLess(len(self.made()), len(compress_plan.LADDER))
        # The estimates are close to the real sizes.
        for step, size in real.items():
            with self.subTest(step=step):
                self.assertAlmostEqual(estimated[step] / size, 1, delta=0.25)

    @staticmethod
    def limits(source: Path, selected: int | None) -> list[int]:
        return limit_plan.limits_of_interest(os.path.getsize(source), selected)

    def test_sequence_numbers_only_go_up(self):
        source = scan_pages_pdf(self.folder, 8)
        self.start(source)
        self.wait_idle()
        seqs = [e["seq"] for e in self.events]
        self.assertTrue(seqs)
        self.assertEqual(seqs, sorted(seqs))
        self.assertEqual(len(set(seqs)), len(seqs))
        self.assertTrue(all(e["session"] for e in self.events))

    def wait_for_estimates(self):
        deadline = time.monotonic() + WAIT_S
        while not any("estimates" in e for e in self.events) and time.monotonic() < deadline:
            time.sleep(0.02)

    def test_changing_tab_while_sampling_keeps_the_sample(self):
        # Size limit → Quality → Size limit, three times, while the sample is
        # being estimated: the PDF is looked at (and sampled) once, and the
        # sample is never given up for this.
        source = scan_pages_pdf(self.folder, 8)
        session = self.start(source)
        self.wait_for_estimates()
        for _ in range(3):
            self.start(source, compress_plan.QUALITY, session)
            self.wait_idle()
            self.start(source, compress_plan.SIZE_LIMIT, session)
        self.wait_idle()
        self.assertEqual(self.worker.count("sample_open"), 1)
        self.assertEqual(len(self.estimates.snapshot(session)["estimates"]), len(compress_plan.LADDER))
        # Every step estimated: the worker has let the sample go.
        self.assertNotIn(f"sample-{session}", sampling._SAMPLES)

    def test_a_closed_dialog_lets_the_sample_go(self):
        source = scan_pages_pdf(self.folder, 8)
        session = self.start(source)
        self.wait_for_estimates()
        self.estimates.stop(discard=True)
        self.wait_idle()
        self.assertNotIn(f"sample-{session}", sampling._SAMPLES)

    def test_a_file_with_no_images_isnt_sampled(self):
        source = self.folder / "text.pdf"
        doc = pymupdf.open()
        for n in range(40):
            doc.new_page().insert_text((72, 100), f"Page {n}, text only " * 8, fontsize=12)
        doc.save(source)
        doc.close()
        session = self.start(source)
        self.estimates.select(session, os.path.getsize(source) // 2)
        self.wait_idle()
        self.assertIsNone(self.estimates.snapshot(session)["estimates"])
        self.assertEqual(self.made(), [compress_plan.LAST_STEP])


class SmallFileTests(_Estimates):
    def test_a_small_file_has_no_sample_and_keeps_the_old_order(self):
        source = scan_pages_pdf(self.folder, 3)
        session = self.start(source)
        self.wait_idle()
        snap = self.estimates.snapshot(session)
        self.assertIsNone(snap["estimates"])
        self.assertEqual(self.worker.count("sample_step"), 0)
        made = self.made()
        self.assertTrue(made)
        self.assertEqual(made, [s for s in compress_plan.CHECK_ORDER if s in made])
        real = {s: row["size"] for s, row in snap["steps"].items()}
        self.assertEqual(run_plan(real, limit_plan.limits_of_interest(os.path.getsize(source), None)), made)


class TemporaryFileTests(_Estimates):
    def test_a_file_that_is_no_answer_is_deleted_at_once(self):
        source = scan_with_photos_pdf(self.folder)
        deleted_while_checking = []
        original = self.scratch.discard

        def discard(path):
            if path and self.estimates._thread is not None:
                deleted_while_checking.append(path)
            original(path)

        with mock.patch.object(self.scratch, "discard", side_effect=discard):
            session = self.start(source)
            self.wait_idle()
        self.assertTrue(deleted_while_checking)
        snap = self.estimates.snapshot(session)["steps"]
        real = {s: row["size"] for s, row in snap.items()}
        limits = limit_plan.limits_of_interest(os.path.getsize(source), None)
        kept = (limit_plan.answers(real, None, limits) | set(compress_plan.LEVEL_STEPS.values())) & set(real)
        self.assertEqual(len(self.scratch_files()), len(kept))
        self.assertEqual(sorted(self.scratch.tracked()), sorted(str(self.folder / n) for n in self.scratch_files()))


class DiskSpaceTests(_Estimates):
    def test_with_little_free_space_files_arent_kept_and_compress_makes_the_step_again(self):
        source = scan_with_photos_pdf(self.folder)
        size = os.path.getsize(source)
        tight = 2 * size + scratch.KEEP_RESERVE - 1
        with mock.patch.object(scratch, "free_bytes", return_value=tight):
            session = self.start(source)
            self.wait_idle()
            self.assertEqual(self.scratch_files(), [])
            snap = self.estimates.snapshot(session)
            self.assertFalse(snap["nospace"])
            real = {s: row["size"] for s, row in snap["steps"].items()}
            self.assertTrue(real)
            step, settled, planned = self.estimates.decide(session, 100 * KB)
            self.assertTrue(settled)
            given = []

            def take(ctx, each):
                given.append(self.estimates.wait_for(session, each, ctx))
                return given[-1]

            result = tools.compress_to_limit(
                fx.InlineContext(), tools.Source(str(source)), str(self.folder / "out.pdf"), step, 100 * KB,
                "100 KB", self.scratch, take, [].append, real, planned,
            )
        self.assertEqual(given, [None])  # no file kept: the job made it again
        self.assertAlmostEqual(result["after"], real[step], delta=SAME_FILE_BYTES)
        self.assertEqual(self.scratch_files(), [])

    def test_no_room_for_one_step_says_so_and_another_folder_retries(self):
        source = scan_with_photos_pdf(self.folder)
        size = os.path.getsize(source)
        other = Path(tempfile.mkdtemp())
        try:
            with mock.patch.object(scratch, "free_bytes", side_effect=lambda f: size - 1 if Path(f) == self.folder else None):
                session = self.start(source)
                self.wait_idle()
                snap = self.estimates.snapshot(session)
                self.assertTrue(snap["nospace"])
                self.assertEqual(snap["steps"], {})
                self.assertEqual(self.worker.count("compress_images"), 0)
                self.assertIn({"session": session, "seq": snap["nospace_seq"], "nospace": True}, self.events)
                # Save in changed to another drive: the page asks again there.
                self.start(source, compress_plan.SIZE_LIMIT, session, folders=[str(other)])
                self.wait_idle()
            snap = self.estimates.snapshot(session)
            self.assertFalse(snap["nospace"])
            self.assertTrue(snap["steps"])
            self.assertIn({"session": session, "seq": snap["nospace_seq"], "nospace": False}, self.events)
            self.assertTrue(self.scratch_files(other))
        finally:
            self.estimates.stop(discard=True)
            self.wait_idle()
            for name in os.listdir(other):
                os.remove(other / name)
            other.rmdir()


    def test_free_space_is_read_where_the_file_was_really_written(self):
        # Save in refuses files (and its drive reads as full); the original's
        # folder takes them and has room: the check is made there.
        source = scan_with_photos_pdf(self.folder)
        refusing = Path(tempfile.mkdtemp())
        real_mkstemp = tempfile.mkstemp

        def mkstemp(dir=None, **kw):
            if Path(dir) == refusing:
                raise PermissionError(13, "Access is denied")
            return real_mkstemp(dir=dir, **kw)

        try:
            with mock.patch.object(scratch.tempfile, "mkstemp", side_effect=mkstemp), \
                    mock.patch.object(scratch, "free_bytes", side_effect=lambda f: 0 if Path(f) == refusing else None):
                session = self.start(source, folders=[str(refusing), str(self.folder)])
                self.wait_idle()
            snap = self.estimates.snapshot(session)
            self.assertFalse(snap["nospace"])
            self.assertTrue(snap["steps"])
        finally:
            refusing.rmdir()

    def test_a_drive_that_fills_up_while_writing_says_no_space(self):
        source = scan_with_photos_pdf(self.folder)
        with mock.patch.object(tools, "compress_attempt", side_effect=RuntimeError("cannot write")), \
                mock.patch.object(safe_write, "_low_on_space", return_value=True):
            session = self.start(source)
            self.wait_idle()
        snap = self.estimates.snapshot(session)
        self.assertTrue(snap["nospace"])
        self.assertEqual(snap["steps"], {})  # not "failed", which would read as too small
        self.assertEqual(self.scratch_files(), [])

    def test_a_step_that_fails_on_a_roomy_drive_is_a_failure(self):
        source = scan_with_photos_pdf(self.folder)
        with mock.patch.object(tools, "compress_attempt", side_effect=RuntimeError("bad image")), \
                mock.patch.object(safe_write, "_low_on_space", return_value=False):
            session = self.start(source)
            self.wait_idle()
        snap = self.estimates.snapshot(session)
        self.assertFalse(snap["nospace"])
        self.assertTrue(all(row["state"] == "failed" for row in snap["steps"].values()))

    def test_with_little_free_space_a_job_takes_its_step_without_making_it_twice(self):
        source = scan_with_photos_pdf(self.folder)
        tight = 2 * os.path.getsize(source) + scratch.KEEP_RESERVE - 1
        e = self.estimates
        e._session, e._source, e._folders = 5, tools.Source(str(source)), [str(self.folder)]
        with mock.patch.object(scratch, "free_bytes", return_value=tight):
            attempt = e.wait_for(5, 4, fx.InlineContext())
            self.wait_idle()
        self.assertIsNotNone(attempt)  # the file was kept for the job
        self.assertTrue(os.path.isfile(attempt.path))
        self.assertEqual(self.worker.count("compress_images"), 1)
        self.scratch.discard(attempt.path)


class DropToNextStepTests(unittest.TestCase):
    """Compress on a step decided on an estimate that turns out over the
    limit: the next step down that really fits."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        folder = Path(cls._tmp.name)
        cls.source = scan_with_photos_pdf(folder)
        made = ScratchFiles(folder / "manifest.json")
        cls.real = {}
        for step, recipe in enumerate(compress_plan.LADDER):
            tmp = made.new([str(folder)])
            attempt = tools.compress_attempt(fx.InlineContext(), tools.Source(str(cls.source)), tmp, recipe)
            cls.real[step] = attempt.size
            made.discard(tmp)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def setUp(self):
        self._out = tempfile.TemporaryDirectory()
        self.folder = Path(self._out.name)
        self.scratch = ScratchFiles(self.folder / "manifest.json")

    def tearDown(self):
        self._out.cleanup()

    def compress(self, step, limit, planned, known=None, held=None) -> tuple[dict, list[int], str]:
        asked = []

        def none(ctx, each):
            asked.append(each)
            return None  # nothing made in advance: the job makes each step

        with self.assertLogs("lector.features.file_tools.tools", "WARNING") as logs:
            result = tools.compress_to_limit(
                fx.InlineContext(), tools.Source(str(self.source)), str(self.folder / "out.pdf"), step, limit,
                "X KB", self.scratch, none, (held if held is not None else []).append, known or {}, planned,
            )
        return result, asked, "\n".join(logs.output)

    def between(self, better: int, worse: int) -> int:
        """A limit `worse` fits and `better` doesn't."""
        limit = (self.real[worse] + self.real[better]) // 2 * 100 // 97
        self.assertTrue(compress_plan.fits(self.real[worse], limit))
        self.assertFalse(compress_plan.fits(self.real[better], limit))
        return limit

    def test_a_drop_into_black_and_white_says_so(self):
        limit = self.between(6, 7)
        result, asked, logs = self.compress(6, limit, {"size": limit // 2, "kind": compress_plan.READABLE})
        self.assertEqual(asked, [6, 7])
        self.assertAlmostEqual(result["after"], self.real[7], delta=SAME_FILE_BYTES)
        self.assertEqual(result["dropped"], "bw")
        self.assertIn(f"estimated {limit // 2}", logs)
        self.assertIn(str(self.real[6]), logs)
        self.assertEqual(list(self.folder.glob(f"{PREFIX}*")), [])

    def test_a_drop_in_colour_says_the_quality_was_lowered(self):
        limit = self.between(3, 4)
        result, _asked, _logs = self.compress(3, limit, {"size": limit // 2, "kind": compress_plan.SHARP})
        self.assertAlmostEqual(result["after"], self.real[4], delta=SAME_FILE_BYTES)
        self.assertEqual(result["dropped"], "quality")

    def test_a_step_known_not_to_fit_is_skipped(self):
        limit = self.between(6, 7)
        result, asked, _logs = self.compress(5, limit, {"size": 1, "kind": "good"}, known={6: self.real[6]})
        self.assertEqual(asked, [5, 7])  # 6 was known to be over
        self.assertAlmostEqual(result["after"], self.real[7], delta=SAME_FILE_BYTES)

    def test_a_step_that_fits_drops_nothing(self):
        limit = self.real[3] * 100 // 97 + 1
        asked = []
        result = tools.compress_to_limit(
            fx.InlineContext(), tools.Source(str(self.source)), str(self.folder / "out.pdf"), 3, limit,
            "X KB", self.scratch, lambda ctx, each: asked.append(each), [].append, {}, None,
        )
        self.assertIsNone(result["dropped"])
        self.assertEqual(asked, [3])

    def test_a_lower_step_that_fails_is_passed_over(self):
        limit = self.between(6, 7)
        real_attempt = tools._attempt_into_scratch

        def failing(ctx, source, recipe, *args):
            if recipe == compress_plan.LADDER[7]:
                raise tools.JobFailed("worker lost")
            return real_attempt(ctx, source, recipe, *args)

        with mock.patch.object(tools, "_attempt_into_scratch", side_effect=failing):
            result, asked, logs = self.compress(6, limit, {"size": limit // 2, "kind": compress_plan.READABLE})
        self.assertEqual(asked, [6, 7, 8])
        self.assertAlmostEqual(result["after"], self.real[8], delta=SAME_FILE_BYTES)
        self.assertIn("step 7 failed", logs)
        self.assertEqual(list(self.folder.glob(f"{PREFIX}*")), [])

    def test_the_chosen_step_failing_fails_the_job(self):
        with mock.patch.object(tools, "_attempt_into_scratch", side_effect=tools.JobFailed("worker lost")):
            with self.assertRaises(tools.JobFailed):
                tools.compress_to_limit(
                    fx.InlineContext(), tools.Source(str(self.source)), str(self.folder / "out.pdf"), 6, 10 * MB,
                    "X KB", self.scratch, lambda ctx, each: None, [].append, {}, None,
                )

    def test_nothing_fits_holds_the_smallest(self):
        held = []
        result, _asked, _logs = self.compress(8, 1000, {"size": 500, "kind": "bw"}, held=held)
        self.assertTrue(result["unreachable"])
        self.assertAlmostEqual(result["smallest"], min(self.real[8], self.real[9]), delta=SAME_FILE_BYTES)
        self.assertEqual([h.size for h in held], [result["smallest"]])
        self.assertFalse((self.folder / "out.pdf").exists())
        self.scratch.discard(held[0].path)


class JobWaitTests(_Estimates):
    """A Compress job waiting on the estimate thread for its step."""

    def seed(self, source: Path, estimates: dict) -> int:
        """A session as if its sample were done, with nothing made yet."""
        e = self.estimates
        e._session, e._source, e._folders = 5, tools.Source(str(source)), [str(self.folder)]
        e._look = {"sampled": False}
        e._estimates = {step: {"size": size, "kind": "good"} for step, size in estimates.items()}
        return 5

    def test_a_step_that_misses_is_taken_from_the_thread_not_made_twice(self):
        source = scan_with_photos_pdf(self.folder)
        real = {}
        for step in (3, 4):
            tmp = self.scratch.new([str(self.folder)])
            real[step] = tools.compress_attempt(fx.InlineContext(), tools.Source(str(source)), tmp, compress_plan.LADDER[step]).size
            self.scratch.discard(tmp)
        limit = (real[4] + real[3]) // 2 * 100 // 97
        # Step 3 "estimated" with room to spare, but really over the limit.
        session = self.seed(source, {**{s: 99 * MB for s in range(3)}, **{s: limit // 2 for s in range(3, 10)}})
        step, settled, planned = self.estimates.decide(session, limit)
        self.assertEqual((step, settled), (3, True))
        with self.assertLogs("lector.features.file_tools.tools", "WARNING"):
            result = tools.compress_to_limit(
                fx.InlineContext(), tools.Source(str(source)), str(self.folder / "out.pdf"), step, limit, "X KB",
                self.scratch, lambda ctx, each: self.estimates.wait_for(session, each, ctx), [].append,
                self.estimates.real_sizes(session), planned,
            )
        self.assertEqual(result["dropped"], "quality")
        # Steps 3 and 4, each made once, by the thread, and taken by the job.
        self.assertEqual(self.made(), [3, 4])
        self.assertEqual(self.worker.count("compress_images"), 2)
        self.wait_idle()
        self.assertEqual(self.scratch_files(), [])

    def test_a_late_give_way_doesnt_undo_what_the_job_asked_for(self):
        # The job took step 3 (over the limit after all) and is now waiting
        # for step 4, which is being made; the bridge's give_way(3) arrives
        # only now.
        source = scan_with_photos_pdf(self.folder)
        self.seed(source, {})
        e = self.estimates
        with e._lock:
            e._taken, e._keep = {3}, 4
            e._running, e._running_session = 4, 5
            e._thread = threading.current_thread()  # stands in for the thread making 4
        try:
            self.estimates.give_way(5, 3)
            self.assertEqual(self.estimates._keep, 4)
            self.assertFalse(self.estimates._abort.is_set())
        finally:
            with e._lock:
                e._thread, e._running, e._keep = None, None, None

    def test_a_job_doesnt_wait_for_a_thread_that_died(self):
        source = scan_with_photos_pdf(self.folder)
        session = self.seed(source, {})
        with mock.patch.object(self.estimates, "_make", side_effect=RuntimeError("boom")):
            with self.assertLogs("lector.features.file_tools.estimates", "ERROR"):
                started = time.monotonic()
                self.assertIsNone(self.estimates.wait_for(session, 9, fx.InlineContext()))
        self.assertLess(time.monotonic() - started, 10)


class BridgeDecisionTests(unittest.TestCase):
    def test_a_pill_enabled_on_an_estimate_can_be_compressed(self):
        state = tempfile.TemporaryDirectory()
        self.addCleanup(state.cleanup)
        estimates = CompressEstimates(InlineWorker(), ScratchFiles(Path(state.name) / "m.json"), lambda e: None)
        estimates._session = 5
        estimates._source = tools.Source("x.pdf")
        estimates._estimates = {step: {"size": 700_000, "kind": "good"} for step in range(10)}
        self.assertEqual(estimates.decide(5, 1 * MB), (0, True, {"size": 700_000, "kind": "good"}))
        self.assertEqual(estimates.decide(5, 850_000)[:2], (None, False))
        self.assertEqual(estimates.decide(6, 1 * MB), (None, False, None))


if __name__ == "__main__":
    unittest.main()
