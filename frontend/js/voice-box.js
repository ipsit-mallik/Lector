// The sidebar's voice status box, shared by Home and Settings (Milestone 8.12).
//
// It was inline in home.js, which was fine while Home was the only screen with
// one. Settings' voice commands are real now and needed the same honest
// indicator, so it lives here once.
//
// "Honest" is the point. The box starts in a neutral "checking" state rather
// than claiming "ready" before the engine has answered, says "ready" only once
// it has *and* the page's voice scope was applied, and says plainly when either
// isn't true. Saying "VOICE READY" next to commands that did nothing was the
// bug this replaces.

const VOICE_IDLE_DETAIL =
  "Hold Space and say what you want. Offline — nothing leaves this machine.";
const HEARD_LINGER_MS = 2500;

// The resting copy has to name the way in that the reader actually has: a
// reader who turned push-to-talk off and the wake phrase on is told to hold
// Space by the old wording, which is advice that does nothing.
function idleDetail(wake, pushToTalk) {
  if (wake && pushToTalk) {
    return "Hold Space, or say “Hey Lector”. Offline — nothing leaves this machine.";
  }
  if (wake) {
    return "Say “Hey Lector”, then your command. Offline — nothing leaves this machine.";
  }
  return VOICE_IDLE_DETAIL;
}

// What a screen reader hears for each state. The visible text changes with every
// partial transcript while someone speaks, so announcing *that* would chatter
// over the very words being said; one short message per state change does not.
function announcementFor({ state, text, error }) {
  switch (state) {
    case "unavailable":
      return error ? `Voice unavailable. ${error}` : "Voice unavailable";
    case "listening":
      return "Listening";
    case "heard":
      return text ? `Heard: "${text}"` : "Didn't catch that";
    case "off":
      return "Voice off";
    default:
      return "Voice ready";
  }
}

/**
 * @param {{box: HTMLElement, state: HTMLElement, detail: HTMLElement,
 *   live?: HTMLElement}} els  `live` is a role="status" element that carries the
 *   screen-reader summary; without one the box is silent to assistive tech, as
 *   it was before.
 * @returns {{render: Function, attach: Function, showUnavailable: Function}}
 *   `render` is what `initVoice` is given as its `onState`; `attach` hands back
 *   the controller `initVoice` returns, so a lingering "heard" message can
 *   settle through the engine; `showUnavailable` reports a failure that
 *   happened before the engine could be asked at all.
 */
function createVoiceBox({ box, state: stateEl, detail: detailEl, live }) {
  let controller = { rest: () => {} };
  let heardTimer = null;
  let lastAnnouncement = "";

  // Written only when the message changes, so a stream of partial transcripts
  // is announced once ("Listening") and a repeated state is not re-read.
  function announce(info) {
    if (!live) return;
    const message = announcementFor(info);
    if (message === lastAnnouncement) return;
    lastAnnouncement = message;
    live.textContent = message;
  }

  function render({ state, text, error, wake, pushToTalk, viaWake }) {
    announce({ state, text, error });
    clearTimeout(heardTimer);
    box.classList.toggle("starting", false);
    box.classList.toggle("listening", state === "listening");
    box.classList.toggle("unavailable", state === "unavailable" || state === "off");

    switch (state) {
      case "unavailable":
        stateEl.textContent = "VOICE UNAVAILABLE";
        // Naming the fix in place of the generic copy: the reader can act on it
        // before opening anything.
        detailEl.textContent = error || "No speech model installed.";
        break;
      case "listening":
        stateEl.textContent = "LISTENING";
        detailEl.textContent = text
          ? `"${text}"`
          : viaWake
            ? "Go ahead — say your command."
            : "Go ahead — release Space when you're done.";
        break;
      case "heard":
        stateEl.textContent = "HEARD";
        detailEl.textContent = text ? `"${text}"` : "Didn't catch that.";
        // Back to rest through the engine rather than by rendering "idle"
        // directly: only it knows whether resting means ready, off, or
        // unavailable, and which activation modes to word the copy for.
        heardTimer = setTimeout(() => controller.rest(), HEARD_LINGER_MS);
        break;
      case "off":
        // Not a failure: docs/PRD.md makes voice an accelerator, and switching
        // it off is a supported choice rather than something to nag about.
        stateEl.textContent = "VOICE OFF";
        detailEl.textContent = "Turn on push-to-talk or the wake phrase in Settings.";
        break;
      default:
        stateEl.textContent = "VOICE READY";
        detailEl.textContent = idleDetail(wake, pushToTalk);
    }
  }

  return {
    render,
    attach(nextController) {
      controller = nextController;
    },
    showUnavailable(message) {
      render({ state: "unavailable", error: message });
    },
  };
}
