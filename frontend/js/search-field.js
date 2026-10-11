// Behaviour for the one search field (frontend/shared/search-field.css): its
// clear (x) button and its "listening" state. Everything else a field does —
// what typing filters, its shortcuts — belongs to the page that owns it.
//
// The clear button is wired once, here, for every field on the page, including
// ones added later ("What can I say?" is inserted by script). Whether it shows
// is CSS's job (:placeholder-shown), so a field whose value is set by script
// needs no call to keep it right.

const SEARCH_FIELD_LISTENING_PLACEHOLDER = "Listening…";

// Empties the field and tells its owner through the same "input" event typing
// raises, so each page's own filter runs exactly as if the reader had deleted
// the text, then hands focus back to the field.
document.addEventListener("click", (ev) => {
  const clear = ev.target.closest && ev.target.closest(".search-field-clear");
  if (!clear) return;
  const input = clear.closest(".search-field").querySelector(".search-field-input");
  input.value = "";
  input.dispatchEvent(new Event("input", { bubbles: true }));
  input.focus();
});

// Voice is typing into this field (dictation): the halo pulses in the voice
// colour, the mic shows, and the placeholder says so. Turning it off puts the
// field's own placeholder back, however many times it was turned on.
function setSearchFieldListening(input, listening) {
  const field = input.closest(".search-field");
  const saved = "idlePlaceholder" in input.dataset;
  if (listening && !saved) {
    input.dataset.idlePlaceholder = input.placeholder;
  } else if (!listening && saved) {
    input.placeholder = input.dataset.idlePlaceholder;
    delete input.dataset.idlePlaceholder;
  }
  if (listening) input.placeholder = SEARCH_FIELD_LISTENING_PLACEHOLDER;
  field.classList.toggle("is-listening", listening);
}
