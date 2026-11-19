"""Organise pages (Milestone F2): a draft of the open document's pages,
changed by actions with their own Undo/Redo and applied as one edit on Done
(docs/ARCHITECTURE.md § "Page changes")."""
import os
import time
from pathlib import Path

from lector.bridge import checks
from lector.bridge.core import Controller, log
from lector.features.dialogs import browser as dialog_browser
from lector.features.file_tools import destinations as tool_destinations
from lector.features.file_tools import engine as tool_engine
from lector.features.file_tools import formats as tool_formats
from lector.features.file_tools import naming as tool_naming
from lector.features.reading import open_errors, page_plan
from lector.shared import app_log

# Organise's timing probe (frontend/pages/organise-timing.js, debug runs
# only): the fields one report may carry, all numbers but `kind`.
ORGANISE_TIMING_FIELDS = ("pages", "bridge", "python", "render", "frame", "settled", "thumbs", "thumbsDone")
ORGANISE_TIMING_KINDS = ("move", "shift", "rotate", "delete", "insert", "undo", "redo")

# Making an image or e-book into pages for Organise's Insert… (an e-book is
# laid out first, which can take a while).
INSERT_CONVERT_TIMEOUT_S = 180

# What Done says when its page changes couldn't be applied.
PAGE_CHANGES_FAILED = "Couldn't apply the page changes. The PDF is as it was; your changes are still here."


class OrganiseController(Controller):
    def _organise_view(self, **extra) -> dict:
        history = self._organise
        return {
            "ok": True,
            "pages": [
                {"key": p.key, "source": p.source, "index": p.index, "turn": p.turn, "new": p.is_new}
                for p in history.current
            ],
            "can_undo": history.can_undo,
            "can_redo": history.can_redo,
            "steps": history.steps,
            "debug": app_log.debug_enabled(),
            **extra,
        }

    def organise_begin(self) -> dict:
        """Open Organise on the open document (or carry on with its draft,
        when the page reloads). `{ok: False, message}` when its pages can't
        be changed."""
        message = self._core.organise_block_message()
        if message is not None:
            return {"ok": False, "message": message}
        if self._organise is None:
            self._organise = page_plan.PlanHistory(page_plan.initial(self._doc.page_count))
        return self._organise_view(page_index=self._doc.page_index)

    def organise_action(self, kind: str, args: dict) -> dict:
        """One Organise action on the draft: `rotate` (`positions`,
        `degrees` ±90), `delete` (`positions`), `move` (`positions`, `gap`),
        `shift` (`positions`, `step` ±1) or `insert` (`source`, from
        `organise_add_source`, and `gap`). Returns the new draft and the
        pages to select (where the moved or inserted pages are). `{ok:
        False, reason}` for one the plan refuses (`page_plan.EVERY_PAGE`,
        ...), with the draft as it stays: the page has already shown the
        action (organise-plan.js) and goes back to this."""
        started = time.perf_counter()
        if self._organise is None:
            return {"ok": False, "reason": page_plan.BAD_POSITIONS}
        if not isinstance(args, dict):
            return self._organise_refused(page_plan.BAD_POSITIONS)
        try:
            return self._organise_act(kind, args)
        finally:
            log.debug("organise %s: %.1f ms in Python", kind if kind in ORGANISE_TIMING_KINDS else "?",
                      (time.perf_counter() - started) * 1000)

    def _organise_act(self, kind: str, args: dict) -> dict:
        plan = self._organise.current
        positions = args.get("positions")
        try:
            if kind == "insert":
                new, selection = self._organise_insert(plan, args.get("source"), args.get("gap"))
            elif not isinstance(positions, list):
                raise page_plan.PlanError(page_plan.BAD_POSITIONS)
            else:
                new, selection = self._organise_step(plan, kind, positions, args)
        except page_plan.PlanError as exc:
            return self._organise_refused(exc.reason)
        changed = self._organise.push(new)
        return self._organise_view(changed=changed, selection=list(selection))

    @staticmethod
    def _is_turn(value) -> bool:
        """Exactly one of a plan's turns: an int (not a bool, not 90.0)."""
        return type(value) is int and value in page_plan.TURNS

    def _organise_refused(self, reason: str) -> dict:
        """A refused action or step: why, and the draft as it is."""
        return {**self._organise_view(), "ok": False, "reason": reason}

    @staticmethod
    def _one_of(value, allowed: tuple[int, ...]) -> int:
        """`value` if it is exactly one of `allowed` (an int: not a bool,
        not 90.0); PlanError otherwise."""
        if type(value) is not int or value not in allowed:
            raise page_plan.PlanError(page_plan.BAD_POSITIONS)
        return value

    @classmethod
    def _organise_step(cls, plan: page_plan.Plan, kind: str, positions: list, args: dict):
        if kind == "rotate":
            degrees = cls._one_of(args.get("degrees"), (page_plan.QUARTER_TURN, -page_plan.QUARTER_TURN))
            return page_plan.rotate(plan, positions, degrees), page_plan.checked_positions(plan, positions)
        if kind == "delete":
            return page_plan.delete(plan, positions), ()
        if kind == "move":
            return page_plan.move(plan, positions, args.get("gap"))
        if kind == "shift":
            return page_plan.shift(plan, positions, cls._one_of(args.get("step"), (-1, 1)))
        raise page_plan.PlanError(page_plan.BAD_POSITIONS)

    def _organise_insert(self, plan: page_plan.Plan, source, gap):
        """Every page of a file `organise_add_source` holds, put in at `gap`."""
        count = self._doc.page_source_count(source) if isinstance(source, str) else None
        if count is None:
            raise page_plan.PlanError(page_plan.BAD_SOURCE)
        return page_plan.insert(plan, gap, source, count)

    def organise_add_source(self, path: str, password: str | None = None) -> dict:
        """The file Insert… was pointed at, opened and held until Organise
        ends (a PDF, unlocked with `password`; an image or e-book made into
        pages at their own size first, as Convert's "Same as image"). Returns
        `{ok: True, source, pages, name}`, or `{ok: False, reason, error,
        name}`: reason "password" asks the page for the password (only held
        in memory, with the file) and "wrong_password" asks again."""
        name = os.path.basename(path) if isinstance(path, str) else ""

        def refused(reason: str, error: str) -> dict:
            return {"ok": False, "reason": reason, "error": error, "name": name}

        if self._organise is None or not isinstance(path, str) or not (password is None or isinstance(password, str)):
            return refused(open_errors.UNKNOWN, open_errors.MESSAGES[open_errors.UNKNOWN])
        if not os.path.isfile(path):
            return refused(open_errors.MISSING, f"Couldn't find {name}. It may have been moved or deleted.")
        unreadable = refused(open_errors.UNREADABLE, f"Couldn't read {name}. It may be damaged.")
        try:
            if tool_formats.extension(path) == dialog_browser.PDF_EXTENSION:
                data = Path(path).read_bytes()
            elif tool_formats.is_convertible(path):
                job = self._ocr.call(tool_engine.convert_to_pdf_bytes, (path,), timeout=INSERT_CONVERT_TIMEOUT_S)
                data, password = job.result(), None
            else:
                return refused(open_errors.NOT_A_PDF, f"{name} can't be inserted. Choose a PDF, an image or an e-book.")
            source, pages = self._doc.add_page_source(data, password)
        except open_errors.OpenError as exc:
            if exc.reason in (open_errors.PASSWORD, open_errors.WRONG_PASSWORD):
                return refused(exc.reason, exc.message)
            return unreadable
        except tool_engine.UnsupportedInput:
            return unreadable
        except Exception:
            log.exception("Couldn't open a file to insert")
            return unreadable
        return {"ok": True, "source": source, "pages": pages, "name": name}

    def organise_forget_source(self, source: str) -> dict:
        """Let go of a file Insert… opened and didn't put in (another was
        chosen, or the dialog was cancelled). One the draft has pages of, now
        or in its Undo/Redo, is kept until Organise ends."""
        if isinstance(source, str) and source != page_plan.DOC:
            if self._organise is None or not self._organise.uses(source):
                self._doc.drop_page_source(source)
        return {"ok": True}

    def extract_numbers(self, positions) -> list[int] | None:
        """The draft's 1-based page numbers at `positions`, or None unless
        Organise is open and they are pages of its draft."""
        if self._organise is None or not self._doc.is_open or not isinstance(positions, list):
            return None
        try:
            return [p + 1 for p in page_plan.checked_positions(self._organise.current, positions)]
        except page_plan.PlanError:
            return None

    def organise_extract_setup(self, positions: list) -> dict:
        """What Extract…'s dialog starts with for the pages at `positions`:
        how many and how they are said ("pages 4–6"), and its Name and "Save
        in" rows, beside the original. `every_page` says whether the
        selection is the whole draft (then nothing can be deleted after)."""
        numbers = self.extract_numbers(positions)
        if numbers is None:
            return {"ok": False}
        path = self._doc.path
        folder = os.path.dirname(path)
        return {
            "ok": True,
            "count": len(numbers),
            "label": tool_naming.pages_label(numbers),
            "every_page": len(numbers) == len(self._organise.current),
            "output": {
                "folder": folder,
                "stem": tool_destinations.extract_stem(path, numbers, folder),
                "extension": tool_destinations.PDF_EXTENSION,
            },
        }

    def suggest_extract_name(self, positions: list, folder: str) -> dict:
        """Extract's Name suggestion in another folder: the first free name
        there."""
        numbers = self.extract_numbers(positions)
        if numbers is None:
            return {"stem": ""}
        if not isinstance(folder, str) or not os.path.isdir(folder):
            folder = os.path.dirname(self._doc.path)
        return {"stem": tool_destinations.extract_stem(self._doc.path, numbers, folder)}

    def organise_debug_timing(self, report: dict) -> dict:
        """One action's timings from Organise's probe (organise-timing.js),
        written to the log at DEBUG: only in a debug run, and only numbers
        (the action's kind is one of a fixed few)."""
        if not app_log.debug_enabled() or not isinstance(report, dict):
            return {"ok": False}
        kind = report.get("kind")
        numbers = {name: report[name] for name in ORGANISE_TIMING_FIELDS if checks.finite(report.get(name))}
        log.debug("organise timing %s %s", kind if kind in ORGANISE_TIMING_KINDS else "?",
                  " ".join(f"{name}={value}" for name, value in numbers.items()))
        return {"ok": True}

    def organise_undo(self) -> dict:
        """Organise's own Undo: the draft one step back, or `{ok: False}`
        (with the draft as it is) when there is nothing to undo."""
        if self._organise is None:
            return {"ok": False}
        if self._organise.undo() is None:
            return self._organise_refused("nothing")
        return self._organise_view()

    def organise_redo(self) -> dict:
        if self._organise is None:
            return {"ok": False}
        if self._organise.redo() is None:
            return self._organise_refused("nothing")
        return self._organise_view()

    def organise_cancel(self) -> dict:
        """Leave Organise without applying the draft: it is dropped, with
        the files Insert opened. Nothing in the open document changes, so no
        snapshot is taken and nothing is recorded."""
        self._organise = None
        self._doc.drop_page_sources()
        return {**self._state(), "pages_changed": False, "history_trimmed": False}

    def organise_thumbnail(self, source: str, index: int, width: int, turn: int = 0) -> dict:
        """A page of the draft, `width` pixels wide (clamped) as the page is
        now, then turned `turn` degrees clockwise (0, 90, 180 or 270): at
        once the grid turns the image it has on screen, and a turned page's
        own image replaces it when this answers."""
        index = checks.page_index(index)
        if not isinstance(source, str) or index is None or not checks.finite(width) or not self._is_turn(turn):
            return {"image": None, "width": 0, "height": 0}
        width = max(checks.MIN_THUMBNAIL_PX, min(int(width), checks.MAX_THUMBNAIL_PX))
        pix = self._doc.render_plan_thumbnail(source, index, width, turn)
        if pix is None:
            return {"image": None, "width": 0, "height": 0}
        return {"image": checks.pixmap_to_png_b64(pix), "width": pix.width, "height": pix.height}

    def organise_done(self) -> dict:
        """Leave Organise, applying the draft to the open document as one
        undoable edit. The reader's state comes back with `pages_changed`
        (whether anything changed) and `history_trimmed` (older changes
        were dropped). If applying fails, the document is as it was, the
        draft stays open and `error` says so."""
        history = self._organise
        if history is None or not self._doc.is_open:
            self._organise = None
            return {**self._state(), "pages_changed": False, "history_trimmed": False}
        try:
            applied = self._doc.apply_page_plan(history.current)
        except Exception:
            log.exception("Applying page changes failed")
            return {**self._state(), "pages_changed": False, "history_trimmed": False, "error": PAGE_CHANGES_FAILED}
        self._organise = None
        self._doc.drop_page_sources()
        if applied:
            self._after_pages_changed()
        return {
            **self._state(),
            "pages_changed": applied,
            "history_trimmed": self._doc.take_history_trimmed(),
        }
