/** Detect the browser's speech-recognition interface without requesting audio access. */
export function speechSupported(window) {
  return Boolean(window.SpeechRecognition || window.webkitSpeechRecognition);
}

/**
 * Create an opt-in dictation controller. Only start() requests microphone access.
 * Transcription is provided by the browser's speech service; this app saves no audio.
 * @returns {object} Supported flag plus start, stop, and cleanup methods.
 */
export function createDictation(window) {
  const Recognition =
    window.SpeechRecognition || window.webkitSpeechRecognition;
  let active = null;
  return {
    supported: speechSupported(window),
    /** Append one dictated passage to a field, keeping provisional text replaceable. */
    start(field, status) {
      if (!Recognition) {
        status(
          "Dictation is unavailable here. You can use your device's keyboard dictation.",
          false,
        );
        return;
      }
      if (active) {
        active.stop();
        return;
      }
      const recognition = new Recognition();
      const original = field.value.trim();
      let failed = false;
      recognition.lang = window.document.documentElement.lang || "en-US";
      recognition.interimResults = true;
      recognition.continuous = false;
      active = recognition;
      recognition.onresult = (event) => {
        const transcript = Array.from(event.results)
          .map((result) => result[0].transcript)
          .join(" ");
        field.value = [original, transcript].filter(Boolean).join(" ");
        field.dispatchEvent(new window.Event("input", { bubbles: true }));
      };
      recognition.onerror = (event) => {
        failed = true;
        status(
          event.error === "not-allowed"
            ? "Microphone access was not granted. You can still type your goal."
            : "Dictation stopped. Try again or continue typing.",
          false,
        );
      };
      recognition.onend = () => {
        if (active === recognition) active = null;
        if (!failed)
          status("Dictation finished. You can edit the text.", false);
      };
      try {
        recognition.start();
        status("Listening… select Stop when you’re done.", true);
      } catch {
        active = null;
        status("Dictation could not start. You can continue typing.", false);
      }
    },
    /** Stop recording and let the browser finish its current recognition result. */
    stop() {
      active?.stop();
    },
    /** Abort recording when the editor closes; detach callbacks from removed fields. */
    destroy() {
      if (active) {
        active.onresult = null;
        active.onerror = null;
        active.onend = null;
        active.abort();
        active = null;
      }
    },
  };
}
