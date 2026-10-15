// Centres the page on the window rather than on the page area. The toolbar
// takes 57px off the area's right and the progress bar 4px off its left, so a
// page centred in the area sits visibly left of the window's middle. The page
// is moved by --page-offset (reading.css turns it into padding on one side of
// the scroller), and only when it fits: a page too wide to sit on the window's
// centre and still keep PAGE_CENTER_CLEARANCE_PX clear of the area's edges
// (the toolbar side holds the scrollbar) goes back to the area's centre.
// Book and Strip alike; reading.js calls centerPageOnWindow() whenever the
// page's width or the area's size may have changed.

// Room kept between the page and the area's edge: the overlay scrollbar's
// grab area (12px) and its inset, so a centred page never runs under it.
const PAGE_CENTER_CLEARANCE_PX = 16;

// How far right (positive) or left of the area's centre the window's centre
// is, for a page of `pageWidth` in an area spanning `areaLeft` to
// `areaLeft + areaWidth` of a `windowWidth` window — or 0 when the page would
// come closer than `clearance` to either edge of the area there.
function pageOffsetForWindow({ windowWidth, areaLeft, areaWidth, pageWidth, clearance = PAGE_CENTER_CLEARANCE_PX }) {
  if (!(pageWidth > 0) || !(areaWidth > 0)) return 0;
  const windowCenter = windowWidth / 2;
  const offset = windowCenter - (areaLeft + areaWidth / 2);
  const left = windowCenter - pageWidth / 2;
  const right = windowCenter + pageWidth / 2;
  if (left < areaLeft + clearance || right > areaLeft + areaWidth - clearance) return 0;
  return Math.round(offset);
}

// Sets the offset on `scroller` for `page` (the page card, or the strip's
// column of pages). Returns whether it changed, so the caller knows the page
// moved and anything drawn over it must be repainted.
function centerPageOnWindow(scroller, page) {
  const area = scroller.getBoundingClientRect();
  const offset = pageOffsetForWindow({
    windowWidth: document.documentElement.clientWidth,
    areaLeft: area.left,
    areaWidth: scroller.clientWidth,
    pageWidth: page.getBoundingClientRect().width,
  });
  const value = `${offset}px`;
  if (scroller.style.getPropertyValue("--page-offset") === value) return false;
  scroller.style.setProperty("--page-offset", value);
  return true;
}
