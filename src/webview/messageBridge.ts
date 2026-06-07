// src/webview/messageBridge.ts
// SPEC §6.3 + §10 — JS↔native bridge. The injected script watches
// Open WebUI's text input and posts the value back to native on a
// 300ms debounce; on a URL change the native side replays the
// saved value into the new page.

// Runs inside the WebView. Finds the chat input, debounces value
// changes, and posts them. Uses generic selectors + a MutationObserver
// so it survives Open WebUI re-renders.
export const TEXT_INPUT_HOOK = `
(function() {
  if (window.__atlasBridgeInstalled) return;
  window.__atlasBridgeInstalled = true;

  let timer = null;
  let lastValue = "";

  const send = function() {
    const el = document.activeElement;
    const value = (el && 'value' in el) ? el.value : "";
    if (value === lastValue) return;
    lastValue = value;
    if (window.ReactNativeWebView) {
      window.ReactNativeWebView.postMessage(JSON.stringify({
        type: 'unsentDraft',
        value: value,
      }));
    }
  };

  const debouncedSend = function() {
    if (timer) clearTimeout(timer);
    timer = setTimeout(send, 300);
  };

  document.addEventListener('input', debouncedSend, true);
  document.addEventListener('keyup', debouncedSend, true);
  document.addEventListener('focusin', debouncedSend, true);
  document.addEventListener('compositionend', debouncedSend, true);

  // Initial flush in case the page loaded with text already typed.
  setTimeout(send, 50);
})();
true;
`;

// Runs after WebView.reload() to re-inject the saved unsent draft.
// Tries the focused element first, then any visible textarea/input.
export const buildReinjectScript = (draft: string): string => {
  const escaped = draft
    .replace(/\\/g, "\\\\")
    .replace(/`/g, "\\`")
    .replace(/\$/g, "\\$")
    .replace(/\n/g, "\\n")
    .replace(/\r/g, "\\r");
  return `
(function() {
  var DRAFT = \`${escaped}\`;
  if (!DRAFT) return;
  var apply = function() {
    var el = document.activeElement;
    if (!el || !('value' in el)) {
      var all = document.querySelectorAll('textarea, input[type="text"], [contenteditable="true"]');
      for (var i = 0; i < all.length; i++) {
        var candidate = all[i];
        if (candidate.offsetParent !== null || candidate === document.activeElement) {
          el = candidate;
          break;
        }
      }
    }
    if (el && 'value' in el) {
      var proto = Object.getPrototypeOf(el);
      var setter = Object.getOwnPropertyDescriptor(proto, 'value');
      if (setter && setter.set) setter.set.call(el, DRAFT); else el.value = DRAFT;
      el.dispatchEvent(new Event('input', { bubbles: true }));
      el.dispatchEvent(new Event('change', { bubbles: true }));
      el.focus();
      return true;
    }
    return false;
  };
  // Proper retry loop. The previous version incremented tries in
  // the condition but never read it inside setTimeout, so it only
  // ran one extra attempt. Open WebUI's React app can take >1s to
  // mount the chat input, so 10x250ms gives us ~2.5s of safety net.
  var tryIn = function(remaining) {
    if (apply()) return;
    if (remaining > 0) setTimeout(function() { tryIn(remaining - 1); }, 250);
  };
  tryIn(10);
})();
true;
`;
};

export type BridgeMessage =
  | { type: "unsentDraft"; value: string }
  | { type: string; value?: unknown };

export const parseBridgeMessage = (raw: string): BridgeMessage | null => {
  try {
    const parsed = JSON.parse(raw);
    if (parsed && typeof parsed === "object" && typeof parsed.type === "string") {
      return parsed as BridgeMessage;
    }
  } catch {
    // Not JSON — ignore.
  }
  return null;
};
