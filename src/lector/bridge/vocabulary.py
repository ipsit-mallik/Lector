"""The voice scope: which screen is listening, what is on screen, and the
recognizer's grammar built from both.

Shared by the reader (a new document empties the viewport), Organise (a page
change does too), highlights and voice, so it lives beside the core rather
than in any one controller. See docs/ARCHITECTURE.md § "Voice context router".
"""
import threading

from lector.features.annotations import highlight_matcher
from lector.features.reading import ocr
from lector.features.voice import router


class VoiceScope:
    def __init__(self, core):
        self._core = core
        # The viewport `start_listening` was last called with, frozen for the
        # duration of the utterance and reused by `highlight_by_voice` — the
        # reader hears the page they were looking at when they started
        # talking, not whatever is on screen by the time recognition finishes.
        self.viewport: list[dict] = []
        # Scanned pages in that viewport whose text was not ready when the
        # recognizer's vocabulary was last built — their words could not
        # have been heard, so a failed highlight there is explained by OCR,
        # not by the reader (see `ocr_outcome`).
        self.ocr_pages: list[int] = []
        # Which screen/dialog is currently listening (Milestone 8.4). Starts
        # at `router.DEFAULT_CONTEXT` (`reading`) rather than `home` so a
        # caller that never makes a `set_voice_context` call — every test in
        # this codebase today, and any future one that doesn't need this —
        # keeps hearing exactly the grammar it did before this router
        # existed. Pages narrow this explicitly once they load.
        self.context = router.DEFAULT_CONTEXT
        # The contexts a modal (the "What can I say?" panel, a dictation
        # session) has pushed itself on top of, innermost last, so closing it
        # can hand back whatever was listening before rather than a context
        # the closing code has to remember to hardcode (Milestone 8.12).
        self.stack: list[str] = []
        # pywebview runs every bridge call on its own thread, so a push and a
        # pop (or a page's own `set_voice_context`) can overlap. One lock keeps
        # the context and its stack consistent with each other.
        self.lock = threading.RLock()

    def refresh(self) -> None:
        """Recompute and apply the recognizer's active grammar.

        The single seam `start_listening`, `update_voice_viewport`, and
        `set_voice_context` all call through whenever something that affects
        the grammar changes — the active context or the visible viewport —
        so the fixed-vocabulary and viewport-vocabulary computation can't
        drift apart the way duplicating it at each call site would risk.

        `DICTATION` (Milestone 8.8) bypasses the usual computation entirely:
        `VoiceEngine.set_vocabulary(None)` puts the recognizer into
        open-vocabulary mode, so viewport widening (meaningless for free
        dictated text) is skipped rather than computed and then ignored.
        """
        if self.context == router.DICTATION:
            self._core.voice.set_vocabulary(None)
            return
        # Asked before the vocabulary is built, because for a scanned page
        # read in an earlier session this is what loads its cached words.
        self.ocr_pages = [
            region["page_index"]
            for region in self.viewport
            if self._core.doc.ocr_status(region["page_index"]) not in (None, ocr.DONE)
        ]
        extra = self._viewport_vocabulary(self.viewport) if self._core.doc.is_open else []
        self._core.voice.set_vocabulary(router.vocabulary_for(self.context) + extra)

    def _viewport_vocabulary(self, viewport: list[dict]) -> list[str]:
        """Normalized words visible in `viewport`, for widening the grammar.

        Grammar-constrained recognition (docs/ARCHITECTURE.md, `engine.py`)
        can only ever return a word that was in its vocabulary when listening
        started — so a spoken highlight phrase needs the *visible* page's
        words added before `start_listening` begins, not looked up after the
        fact when it's too late for Vosk to have heard them.
        """
        words: set[str] = set()
        for region in viewport:
            page_words = self._core.doc.words_as_shown(region["page_index"])
            visible = highlight_matcher.visible_word_indices(
                page_words, region["y0"], region["y1"]
            )
            for i in visible:
                token = highlight_matcher.normalize_word(page_words[i].text)
                if token:
                    words.add(token)
        return sorted(words)

    def ocr_outcome(self) -> dict | None:
        """Why a voice highlight could not have matched, when the reason is a
        scanned page's text: `{"page_index", "status"}` for the first such
        page in the viewport, or None when OCR is not the explanation.

        Covers the pages whose text was missing when the vocabulary was last
        built (`ocr_pages`). One not read yet is started now, so the
        reader who spoke before the dwell prefetch got there is not left
        waiting on nothing; one that finished since reports "done", which
        the frontend words as "say it again".
        """
        for page_index in self.ocr_pages:
            job = self._core.doc.begin_ocr(page_index)
            if job is not None:
                self._core.ocr.submit(job)
            status = self._core.doc.ocr_status(page_index)
            if status is not None:
                return {"page_index": page_index, "status": status}
        return None
