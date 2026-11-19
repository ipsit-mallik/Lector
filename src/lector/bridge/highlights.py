"""Highlights drawn with the mouse: text runs and freehand strokes, the
highlight tool's options, editing a highlight, and undo/redo (see
docs/ARCHITECTURE.md's highlight notes under "Feature boundaries").
Voice highlights are voice.py's, at the same opacities."""
from lector.bridge import checks
from lector.bridge.core import Controller
from lector.features.annotations import highlighter
from lector.features.reading import page_changes
from lector.features.settings import store as settings
from lector.shared import highlight_options

# Most points a freehand stroke may carry across the bridge. The frontend
# simplifies a stroke before sending it, so a long one is a few hundred; this
# only stops a runaway payload being written into the PDF.
MAX_INK_POINTS = 4096

# Longest gesture name `set_highlight_appearance` takes: the editor sends a
# short counter-based id per slider drag; this only bounds what is held on
# the undo stack.
MAX_GESTURE_CHARS = 64


def opacity_for(style: str) -> float:
    """The stored opacity of highlight `style`, as the fraction a PDF takes."""
    return highlight_options.opacity_fraction(settings.get_highlight_opacity(style))


def _ink_points(value, least: int, most: int = MAX_INK_POINTS) -> list[tuple[float, float]] | None:
    """`value` as a list of `least`..`most` finite `(x, y)` points, or None
    if it is anything else. What a stroke from the bridge must be before any
    of it is written into a PDF."""
    if not isinstance(value, (list, tuple)) or not least <= len(value) <= most:
        return None
    points = []
    for point in value:
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            return None
        if not all(checks.finite(c) for c in point):
            return None
        points.append((float(point[0]), float(point[1])))
    return points


class HighlightsController(Controller):
    def highlight_chars(self, page_index: int, start_idx: int, end_idx: int) -> dict:
        """Mark a dragged run of characters in the active colour and style
        (highlight, underline or strike-through) at that style's opacity."""
        style = settings.get_highlight_style()
        self._doc.highlight_char_indices(
            page_index, start_idx, end_idx, settings.get_highlight_color(),
            style=style, opacity=opacity_for(style),
        )
        return self._state()

    def add_ink_stroke(self, page_index, points, straightened=None) -> dict:
        """Draw a freehand marker stroke in the active colour, the reader's
        width and the Highlight style's opacity (so a stroke beside a text
        highlight is the same colour), whichever style is chosen. `points` is the
        stroke as drawn (`[[x, y], ...]` in PDF points on the page as shown,
        like `get_page_chars`'s boxes); `straightened`, when the frontend
        straightened it on release, its two end points. Anything malformed
        draws nothing: this is a bridge boundary. Returns the state with
        `affected_page` (None when nothing was drawn) and the stroke's `id`."""
        state_page, xref = None, None
        drawn = _ink_points(points, 1)
        final = None if straightened is None else _ink_points(straightened, 2, 2)
        index = checks.page_index(page_index)
        valid = (
            self._doc.is_open
            and drawn is not None
            and (straightened is None or final is not None)
            and index is not None
            and index < self._doc.page_count
        )
        if valid:
            xref = self._doc.add_ink_stroke(
                index, drawn, final, settings.get_highlight_color(),
                width=float(settings.get_ink_width()),
                opacity=opacity_for(highlight_options.HIGHLIGHT),
            )
            state_page = index if xref is not None else None
        state = self._state()
        state["affected_page"] = state_page
        state["id"] = xref
        return state

    def get_highlight_options(self) -> dict:
        """Everything the highlight tool's cards show, and the numbers
        it works with, from `highlight_options` so they are the ones written
        into the PDF: the active style, each style's opacity and default (in
        whole percent), the slider's range, snap and tick step, and the
        freehand width (PDF points) with its presets."""
        return {
            "style": settings.get_highlight_style(),
            "styles": list(highlight_options.STYLES),
            "opacity": settings.get_highlight_opacities(),
            "default_opacity": dict(highlight_options.DEFAULT_OPACITY_PCT),
            "opacity_min": highlight_options.OPACITY_MIN_PCT,
            "opacity_max": highlight_options.OPACITY_MAX_PCT,
            "opacity_snap": highlight_options.OPACITY_SNAP_PCT,
            "opacity_tick_step": highlight_options.OPACITY_TICK_STEP_PCT,
            "ink_width": settings.get_ink_width(),
            "ink_widths": list(highlight_options.INK_WIDTH_PRESETS_PT),
        }

    # The setters below ignore a value the popover does not offer rather
    # than raising, as `set_highlight_color` does: this is a bridge boundary.
    # Each returns the options as now stored.

    def set_highlight_style(self, style) -> dict:
        if highlight_options.is_style(style):
            settings.set_highlight_style(style)
        return self.get_highlight_options()

    def set_highlight_opacity(self, style, pct) -> dict:
        if highlight_options.is_style(style) and highlight_options.is_opacity_pct(pct):
            settings.set_highlight_opacity(style, pct)
        return self.get_highlight_options()

    def set_ink_width(self, width) -> dict:
        if highlight_options.is_ink_width(width):
            settings.set_ink_width(int(width))
        return self.get_highlight_options()

    def get_highlight_palette(self) -> list[str]:
        """The five colour names, in the order the swatches show them. Names
        only: each one's colour is theme.css's `--highlight-<name>`."""
        return list(highlighter.COLOR_NAMES)

    def get_highlight_color(self) -> str:
        return settings.get_highlight_color()

    def set_highlight_color(self, name: str) -> dict:
        """Make `name` the active colour. An unknown name is ignored rather
        than raised: this is a bridge boundary. Returns the state, whose
        `highlight_color` is what was actually stored."""
        if name in highlighter.COLOR_NAMES:
            settings.set_highlight_color(name)
        return self._state()

    def get_page_highlights(self, page_index: int) -> dict:
        """Every highlight on one page, so the frontend can make each one a
        clickable, focusable target: `{"width", "height", "highlights":
        [{"id", "kind", "color", "rects", "text"}]}`, sizes and rects in PDF
        points like `get_page_chars`'s (an ink stroke's entry: see
        `_highlight_entry`). `color` is None for a colour another app wrote
        that is not in the palette; `text` is what the highlight covers, for
        the target's accessible name."""
        page_index = checks.page_index(page_index)
        if not self._doc.is_open or page_index is None or page_index >= self._doc.page_count:
            return {"width": 0, "height": 0, "highlights": []}
        width, height = self._doc.page_size_points(page_index)
        return {
            "width": width,
            "height": height,
            "highlights": [self._highlight_entry(page_index, h) for h in self._doc.highlights_on_page(page_index)],
        }

    def _highlight_entry(self, page_index: int, h: highlighter.Highlight) -> dict:
        """One `get_page_highlights` entry. An ink stroke also carries its
        `strokes` (as shown) and `width`, and has no `text`; its one rect is
        the box it paints, which the editor is placed under."""
        ink = isinstance(h.shape, highlighter.InkShape)
        entry = {
            "id": h.xref,
            "kind": "ink" if ink else "text",
            # "highlight", "underline" or "strikethrough"; "ink" for a stroke.
            "style": "ink" if ink else h.shape.style,
            "opacity": round(h.shape.opacity, 4),
            "color": h.color,
            "rects": [
                {"x0": r.x0, "y0": r.y0, "x1": r.x1, "y1": r.y1}
                for r in self._doc.as_shown(page_index, h.rects)
            ],
        }
        if entry["kind"] == "ink":
            entry["strokes"] = self._doc.shown_strokes(page_index, h.shape)
            entry["width"] = h.shape.width
            entry["text"] = None
        else:
            entry["text"] = self._doc.text_in_rects(page_index, h.rects)
        return entry

    def recolor_highlight(self, page_index: int, highlight_id: int, color: str) -> dict:
        changed = self._doc.set_highlight_color(int(page_index), int(highlight_id), color)
        state = self._state()
        state["affected_page"] = int(page_index) if changed else None
        return state

    def set_highlight_appearance(self, page_index, highlight_id, opacity_pct=None, width=None, gesture=None) -> dict:
        """Change how an existing highlight is drawn, from its editor: its
        opacity (whole percent, as the slider gives it) and, for a stroke,
        its width (one of the presets). `gesture` names the slider drag or
        click it came from, so one gesture is one undo step. A value the
        editor does not offer changes nothing (`affected_page` None)."""
        index = checks.page_index(page_index)
        xref = checks.page_index(highlight_id)
        valid = (
            self._doc.is_open
            and index is not None
            and xref is not None
            and index < self._doc.page_count
            and (opacity_pct is None or highlight_options.is_opacity_pct(opacity_pct))
            and (width is None or highlight_options.is_ink_width(width))
            and (gesture is None or (isinstance(gesture, str) and len(gesture) <= MAX_GESTURE_CHARS))
        )
        changed = valid and self._doc.set_highlight_appearance(
            index,
            xref,
            opacity=None if opacity_pct is None else highlight_options.opacity_fraction(opacity_pct),
            width=None if width is None else float(width),
            gesture=gesture,
        )
        state = self._state()
        state["affected_page"] = index if changed else None
        return state

    def remove_highlight(self, page_index: int, highlight_id: int) -> dict:
        removed = self._doc.remove_highlight(int(page_index), int(highlight_id))
        state = self._state()
        state["affected_page"] = int(page_index) if removed else None
        return state

    def undo_highlight(self) -> dict:
        """Undo the last change: a highlight's, or a set of page changes
        (then `pages_changed` is true and the page reloads its layout).
        Nothing while Organise is open: its draft is of the document as it
        is, and its own Undo is `organise_undo`."""
        if self._organise is not None:
            return self._after_history_step(None, None)
        # The kind comes from the step itself, taken atomically: two quick
        # Ctrl+Z may reach a page change the page thought was a highlight.
        return self._after_history_step(*self._doc.step_history(forward=False))

    def redo_highlight(self) -> dict:
        if self._organise is not None:
            return self._after_history_step(None, None)
        return self._after_history_step(*self._doc.step_history(forward=True))

    def _after_history_step(self, kind: str | None, affected_page: int | None) -> dict:
        pages_changed = kind == page_changes.PAGE_CHANGES and affected_page is not None
        if pages_changed:
            self._after_pages_changed()
        state = self._state()
        state["affected_page"] = affected_page
        state["pages_changed"] = pages_changed
        state["history_trimmed"] = self._doc.take_history_trimmed()
        return state
