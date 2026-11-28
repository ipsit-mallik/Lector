// Where Insert pages can put the new pages, and how that is shown
// (docs/DESIGN_SYSTEM.md § "Organise pages" → "Insert"): the places a
// selection allows, the default, the preview strip's items, its caption, and
// how many columns the segmented control can afford. No DOM: the dialog
// (organise-insert.js) draws from these, and tests run them under Node
// (tests/test_organise_insert_places.py).
"use strict";

const OrganiseInsertPlaces = (() => {
  // The control's columns, widest first: one row, then 2×2, then one column
  // as a last resort (only if even two labels side by side would clip).
  const COLUMN_STEPS = [4, 2, 1];

  // `positions`: the selection, 0-based, in any order. `total`: pages in the
  // draft. Each place has its `gap` (how many pages come before the new ones).
  function places(positions, total) {
    const list = [{ value: "start", label: "At the start", gap: 0 }];
    if (positions.length) {
      const first = Math.min(...positions);
      const last = Math.max(...positions);
      list.push({ value: "before", label: `Before page ${first + 1}`, gap: first });
      list.push({ value: "after", label: `After page ${last + 1}`, gap: last + 1 });
    }
    list.push({ value: "end", label: "At the end", gap: total });
    return list;
  }

  function defaultPlace(positions) {
    return positions.length ? "after" : "end";
  }

  // The strip: the first and last page, the pages either side of the gap,
  // "…" for the pages between them (one page between is shown as itself),
  // and the new pages as one block. At most 7 items at any page count.
  // `selected`: a Set of 0-based positions.
  function preview(total, gap, count, selected) {
    const shown = [...new Set([1, gap, gap + 1, total])]
      .filter((page) => page >= 1 && page <= total)
      .sort((a, b) => a - b);
    const items = [];
    const page = (n) => ({ kind: "page", page: n, selected: selected.has(n - 1) });
    if (gap === 0) items.push({ kind: "insert", count });
    shown.forEach((n, i) => {
      const prev = shown[i - 1];
      if (prev !== undefined && n - prev === 2) items.push(page(prev + 1));
      else if (prev !== undefined && n - prev > 2) items.push({ kind: "gap" });
      items.push(page(n));
      if (n === gap) items.push({ kind: "insert", count });
    });
    return items;
  }

  function caption(gap, count, total) {
    const all = total + count;
    if (count === 1) return `Becomes page ${gap + 1} of ${all}`;
    return `Become pages ${gap + 1}–${gap + count} of ${all}`;
  }

  // `labelWidths`: each label's own width (at its selected weight).
  // `trackWidth`: inside the track's padding. `gap`: between segments.
  // `padX`: a segment's own horizontal padding and border.
  function columns(labelWidths, trackWidth, gap, padX) {
    const widest = Math.max(...labelWidths);
    const steps = COLUMN_STEPS.filter((n) => n <= labelWidths.length && n > 1);
    const fits = steps.find((n) => (trackWidth - gap * (n - 1)) / n - padX >= widest);
    return fits || 1;
  }

  return { places, defaultPlace, preview, caption, columns };
})();
