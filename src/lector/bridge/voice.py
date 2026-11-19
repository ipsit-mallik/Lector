"""Voice: activation modes, the microphone, listening, the screen that is
listening, recognized commands sent to the page, and highlights by voice
(docs/ARCHITECTURE.md § "Voice context router")."""
import json
import threading

import webview

from lector.bridge.core import Controller, log
from lector.bridge.highlights import opacity_for
from lector.features.annotations import highlight_matcher, highlighter
from lector.features.annotations.highlighter import words_between
from lector.features.settings import store as settings
from lector.features.voice import command_grammar, reference, router
from lector.features.voice import engine as engine_module
from lector.shared import highlight_options

# `request_microphone`'s error when no capture could be opened. A code, not
# the audio library's text, which can name devices and drivers.
MICROPHONE_UNAVAILABLE = "microphone_unavailable"


class VoiceController(Controller):
    def highlight_by_voice(
        self, query: str, sentence: bool = False, color: str | None = None, style: str | None = None
    ) -> dict:
        """Matches a spoken phrase against the viewport `start_listening` was
        last called with, and writes the highlight if one is found.

        `color` is a one-off ("highlight <words> in green"): it colours this
        highlight only and leaves the active colour as it was. `style` is the
        command's own ("underline <words>" is an underline whichever style
        the popover has), at that style's opacity; None is a highlight.

        `query` is already normalized (lowercase, no punctuation) by
        `command_grammar.parse` — the same treatment `highlight_matcher`
        gives every page word, so the two compare on equal footing. A miss
        is not an error: `docs/PRD.md` treats a wrong highlight as worse than
        no highlight, so this reports `matched: False` for the frontend to
        show as a "didn't catch that" hint rather than guessing.
        """
        pages = []
        for region in self._vocabulary.viewport:
            # The region is part of the page as shown; indices are the same
            # either way, so the match below highlights the same words.
            words = self._doc.words_as_shown(region["page_index"])
            visible = highlight_matcher.visible_word_indices(words, region["y0"], region["y1"])
            pages.append((region["page_index"], words, visible))
        match = highlight_matcher.find_word_match(pages, query)
        state = self._state()
        if match is None:
            state["voice_highlight"] = {
                "matched": False,
                "query": query,
                "ocr": self._vocabulary.ocr_outcome(),
            }
            return state

        words = self._doc.words_on_page(match.page_index)
        start_idx, end_idx = match.start_idx, match.end_idx
        if sentence:
            start_idx, end_idx = highlight_matcher.extend_to_sentence(words, start_idx, end_idx)
        if color not in highlighter.COLOR_NAMES:
            color = settings.get_highlight_color()
        if not highlight_options.is_style(style):
            style = highlight_options.HIGHLIGHT
        self._doc.highlight_word_indices(
            match.page_index, start_idx, end_idx, color, style=style, opacity=opacity_for(style)
        )

        state = self._state()
        state["voice_highlight"] = {
            "matched": True,
            "query": query,
            "color": color,
            "style": style,
            "affected_page": match.page_index,
            "text": " ".join(w.text for w in words_between(words, start_idx, end_idx)),
        }
        return state

    def get_voice_activation(self) -> dict:
        """`{"push_to_talk": bool, "wake_phrase": bool}` — docs/PRD.md's two
        independently toggleable activation modes."""
        return settings.get_voice_activation()

    def set_voice_activation(self, push_to_talk: bool, wake_phrase: bool) -> dict:
        """Store both flags and bring the engine in line with them.

        Returns the stored pair rather than nothing, so the caller renders
        what was actually saved instead of what it optimistically sent.
        """
        settings.set_voice_activation(push_to_talk, wake_phrase)
        self.apply_wake_activation(wake_phrase)
        return settings.get_voice_activation()

    def apply_wake_activation(self, enabled: bool) -> None:
        """Start or stop idle wake listening, off the calling thread.

        Off-thread because starting it can need the speech model, and a load
        takes seconds — the same reason `VoiceEngine.warm_up` exists. Blocking
        a bridge call for that long risks the page that made it navigating
        away before the reply lands, which kills pywebview's callback.
        """
        if not enabled:
            self._voice.stop_wake_listening()
            return
        threading.Thread(
            target=self._voice.start_wake_listening,
            name="voice-wake-start",
            daemon=True,
        ).start()

    def request_microphone(self) -> dict:
        """Open and immediately close the microphone, to make the OS ask.

        Neither Windows nor macOS has an API for "request microphone access"
        that is separate from *using* the microphone: the permission prompt
        is raised by the first capture attempt. So onboarding's "Allow
        microphone" button does the smallest real capture there is, and
        reports what happened.

        Returns `{"granted": bool, "error": str | None}`. A refusal is a
        normal outcome, not an exception — docs/PRD.md requires the app to
        stay fully usable when the reader says no. `error` is a fixed code,
        not the audio library's text (onboarding shows its own copy).
        """
        try:
            import sounddevice

            stream = sounddevice.RawInputStream(
                samplerate=engine_module.SAMPLE_RATE,
                blocksize=engine_module.BLOCK_SIZE,
                dtype="int16",
                channels=engine_module.CHANNELS,
            )
            stream.start()
            stream.stop()
            stream.close()
        except Exception:
            log.warning("The microphone could not be opened", exc_info=True)
            return {"granted": False, "error": MICROPHONE_UNAVAILABLE}
        return {"granted": True, "error": None}

    def get_command_reference(self) -> dict:
        """The categorized command list the discoverability panel renders.

        Assembled from the grammar itself (`voice/reference.py`) rather than
        written out in the frontend, so the panel cannot offer a phrasing the
        recognizer would not accept. Scoped to `self._vocabulary.context`
        (Milestone 8.10), so opening the panel from a screen with its own
        command set shows that screen's commands rather than always the
        reading grammar's.
        """
        return reference.panel(self._vocabulary.context)

    def get_voice_status(self) -> dict:
        """`{"available", "listening", "error"}` for the mic indicator.

        `available: False` is a normal state, not a failure to report loudly:
        docs/PRD.md requires the app to stay fully usable without voice, so
        the frontend renders this as "Voice unavailable" and carries on.
        """
        return self._voice.status()

    def start_listening(self, viewport: list[dict] | None = None) -> dict:
        """Called on push-to-talk keydown. Safe to call repeatedly — the key
        auto-repeats while held.

        `viewport` is the reading view's currently visible page regions —
        `[{"page_index", "y0", "y1"}, ...]` in PDF points on the page as
        shown, like `get_page_chars`'s boxes — so a spoken
        highlight phrase can be matched later. Home has no document open and
        passes nothing, which is also what widens nothing: `set_vocabulary`
        is only called with more than the navigation grammar when a document
        is actually open, so Home's recognizer is unaffected by this.
        """
        self._vocabulary.viewport = viewport or []
        self._vocabulary.refresh()
        return self._voice.start_listening()

    def stop_listening(self) -> dict:
        """Called on push-to-talk keyup. Returns the final recognized phrase,
        which is `""` when nothing intelligible was heard."""
        return self._voice.stop_listening()

    def update_voice_viewport(self, viewport: list[dict] | None = None) -> None:
        """Keep the wake-phrase path's idea of what is on screen current.

        Push-to-talk can gather the viewport at the moment the key goes down,
        because the reader's keypress is what starts the utterance. A wake
        phrase has no such moment: by the time "Hey Lector" has been heard,
        the recognizer for the command that follows is already being built,
        and a grammar-constrained recognizer can only ever return words that
        were in its vocabulary when it was built (see `engine.py`). So the
        reading view pushes the visible region here as it scrolls, and a wake
        simply uses the latest one.

        Cheap to call redundantly — it only recomputes the word list — but
        the frontend still debounces it, because on a fast scroll "latest"
        arrives many times a second.
        """
        self._vocabulary.viewport = viewport or []
        self._vocabulary.refresh()

    def set_voice_context(self, context: str) -> dict:
        """Told by a page or dialog when it gains focus (Milestone 8.4).

        Swaps the recognizer's active grammar to `context`'s own scoped
        commands plus the always-on global ones (undo/redo/help/go home) —
        the same grammar-swap `wake.py` already does between its idle and
        full vocabularies, generalized here to more than two grammars.
        `router.CONTEXTS` is the fixed set of names a caller may pass;
        anything else is rejected rather than silently accepted, since a
        typo'd context name would otherwise leave the reader talking to
        whatever grammar happened to be active before, with no signal that
        their intended context was never applied.

        Returns `{"context": ...}` — the context actually applied, so the
        caller can confirm it rather than assume its own request landed.
        """
        if context not in router.CONTEXTS:
            raise ValueError(f"unknown voice context: {context!r}")
        # A screen declaring itself is a fresh baseline: any modal still on
        # the stack belonged to a page that has since been left, and popping
        # into its context would put the wrong grammar on this one.
        with self._vocabulary.lock:
            self._vocabulary.stack.clear()
            self._vocabulary.context = context
            self._vocabulary.refresh()
            return {"context": self._vocabulary.context}

    def push_voice_context(self, context: str) -> dict:
        """Enter a modal scope, remembering the context it covers (Milestone 8.12).

        For anything that opens on top of a screen and must give the screen's
        grammar back when it closes: the "What can I say?" panel, and dictation
        inside it. `pop_voice_context` undoes exactly one push, so nested modals
        unwind in order. An unknown name is rejected before anything changes.
        """
        if context not in router.CONTEXTS:
            raise ValueError(f"unknown voice context: {context!r}")
        with self._vocabulary.lock:
            self._vocabulary.stack.append(self._vocabulary.context)
            self._vocabulary.context = context
            self._vocabulary.refresh()
            return {"context": self._vocabulary.context}

    def pop_voice_context(self) -> dict:
        """Leave the innermost modal scope and restore the context beneath it.

        With nothing pushed this is a no-op that reports the current context,
        so a close handler that runs twice (Escape then the Close button) can
        never pop past the screen's own grammar.
        """
        with self._vocabulary.lock:
            if self._vocabulary.stack:
                self._vocabulary.context = self._vocabulary.stack.pop()
                self._vocabulary.refresh()
            return {"context": self._vocabulary.context}

    def on_voice_result(self, result: dict) -> None:
        """Fan a recognition result out to the frontend as a DOM event.

        This is the "discrete events only" bridge direction docs/TECH_STACK.md
        describes — Python pushing to JS, where every other method on this
        class is JS pulling from Python. Dispatching a CustomEvent rather than
        calling one named function means Milestone 6's navigation commands and
        Milestone 7's highlight matcher can each subscribe independently
        without this method growing a list of callees.

        Final results carry a `command` key — the parsed intent, or `None`
        when nothing in the grammar matched — and a `clarify` key, non-`None`
        only when Milestone 8.3's near-miss tier has a candidate worth
        surfacing as "did you mean '<candidate>'?" instead of staying silent.
        Interpretation happens here, on the Python side, because
        docs/ARCHITECTURE.md keeps command interpretation in `voice/` and
        leaves the frontend to decide what a given intent *does*: this sends
        `NEXT_PAGE`, not "scroll the strip". Resolution goes through
        `router.resolve` (Milestone 8.4) rather than `command_grammar`
        directly, so which commands a phrase can match depends on
        `self._vocabulary.context` — global commands always, plus whatever the
        active context's own scoped grammar adds.

        Runs on the engine's worker thread, and deliberately swallows failures:
        no window yet (results arriving during teardown) must not kill the
        audio thread.
        """
        try:
            window = webview.windows[0]
        except IndexError:
            return
        if result.get("final"):
            resolved = router.resolve(
                self._vocabulary.context, result.get("text", ""), result.get("alternatives")
            )
            result = {**result, "command": resolved["command"], "clarify": resolved["clarify"]}
            # "highlight <words>" on a scanned page whose text is not read
            # yet reaches here as a bare "highlight": the recognizer drops
            # every word outside its vocabulary. That is no command at all,
            # and would read as "not a command I know" without this.
            if resolved["command"] is None and command_grammar.is_bare_highlight(result.get("text", "")):
                result["ocr"] = self._vocabulary.ocr_outcome()
        payload = json.dumps(result)
        try:
            window.evaluate_js(
                f"window.dispatchEvent(new CustomEvent('lector:voice', {{detail: {payload}}}))"
            )
        except Exception:
            # Usually a page mid-navigation or a window closing. Logged
            # without the payload: it holds what the reader said.
            log.debug("Could not deliver a voice result to the page", exc_info=True)

    def shutdown_voice(self) -> None:
        """Release the microphone. Called when the window closes."""
        self._voice.shutdown()
