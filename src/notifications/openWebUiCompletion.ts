// src/notifications/openWebUiCompletion.ts
// SPEC §7.2 — the WebView-side detector that turns a real Open WebUI
// assistant turn finishing into a bridge message to native, so the
// CompletionBanner can fire on the foregrounded-different-chat path.
//
// The server-side atlas-notifier watches the same data layer
// (`chat:status` events from Open WebUI's WebSocket). This module
// re-derives that signal from the rendered DOM that the iOS WebView
// actually has access to. **Do not paste the server-side shape here**
// — the WS `chat:status` event lives at a different abstraction level
// than the DOM elements that survive a Svelte re-render.
//
// DOM fingerprint (verified against Open WebUI 0.9.x source, both
// `main` and the v0.5.20 lineage):
//
// 1. While a chat is generating, the message input area's right-hand
//    action button is a `<button>` whose only child is an `<svg>` with
//    the square stop-icon path
//    `M2.25 12c0-5.385 4.365-9.75 9.75-9.75s9.75 4.365 9.75 9.75…`
//    When the assistant turn ends, that button is removed and replaced
//    by `<button id="send-message-button">` (or, if the input is empty,
//    the "call" microphone button).
//
// 2. The just-completed assistant message's button row (inside
//    `ResponseMessage.svelte`) gets a `<button class="copy-response-button">`
//    injected as a sibling. So the appearance of a brand-new
//    `.copy-response-button` is also a positive signal that the
//    most-recent assistant turn is done.
//
// We watch BOTH signals in parallel — a single MutationObserver on
// `document.body` — and fire once per assistant turn. The native side
// (`messageBridge.ts` handler) routes the bridge message to
// `bannerController.show(...)` and to `setActiveChatId(chatId)`.
//
// chatId source: the URL pathname matches `/c/<uuid>` on a chat screen.
// If the user is on the Open WebUI home (`/`) or settings, the detector
// stays quiet — there's no chat to notify about.

const STOP_ICON_PATH_FRAGMENT =
  "M2.25 12c0-5.385"; // stop-button SVG path tail
const COPY_RESPONSE_CLASS = "copy-response-button";

// `true;` suffix is intentional — react-native-webview's
// `injectedJavaScript` runs the snippet as an IIFE expression, and a
// trailing `true;` is the convention we use in `TEXT_INPUT_HOOK` to
// signal a successful injection without a return value.
//
// `window.__atlasCompletionInstalled` guards against double-injection
// (which can happen if the WebView reloads and the prior script's
// MutationObserver wasn't torn down).
export const OPEN_WEBUI_COMPLETION_HOOK = `
(function() {
  if (window.__atlasCompletionInstalled) return;
  window.__atlasCompletionInstalled = true;

  var STOP_FRAG = ${JSON.stringify(STOP_ICON_PATH_FRAGMENT)};
  var COPY_CLASS = ${JSON.stringify(COPY_RESPONSE_CLASS)};

  // ----- chat id + title from URL/DOM -----
  function readChatId() {
    var m = location.pathname.match(/^\\/c\\/([0-9a-fA-F-]{8,})/);
    return m ? m[1] : null;
  }
  function readTitle() {
    var t = (document.title || '').trim();
    // Open WebUI appends " | Open WebUI" (or similar) in some themes.
    // Strip the suffix so the banner shows a clean title.
    var pipe = t.indexOf(' | ');
    if (pipe > 0) t = t.slice(0, pipe);
    return t || 'Chat';
  }

  // ----- detection: is the stop button currently mounted? -----
  function hasStopButton() {
    // The stop button is the only button in the input form whose SVG
    // has the square-icon path. We search broadly because the form
    // selector is brittle across OWUI versions.
    var svgs = document.querySelectorAll('button svg path[d]');
    for (var i = 0; i < svgs.length; i++) {
      var d = svgs[i].getAttribute('d') || '';
      if (d.indexOf(STOP_FRAG) === 0) return true;
    }
    return false;
  }

  // ----- detection: did a new .copy-response-button just appear? -----
  // We track the set of chat ids that already own a copy button so
  // re-renders don't double-fire.
  var knownCopyButtons = new Set();

  function countCopyButtons() {
    var btns = document.getElementsByClassName(COPY_CLASS);
    var ids = [];
    for (var i = 0; i < btns.length; i++) {
      ids.push(btns[i]);
    }
    return ids;
  }

  function snapshotKnown() {
    var btns = countCopyButtons();
    for (var i = 0; i < btns.length; i++) {
      // Use the closest assistant-message wrapper's data attr if
      // available; otherwise fall back to a stable position.
      knownCopyButtons.add(btns[i]);
    }
  }
  // Initialize on first install.
  snapshotKnown();

  // ----- main loop -----
  var wasGenerating = hasStopButton();
  var lastFiredChatId = null;

  function postCompletion(chatId, title) {
    if (!chatId) return;
    if (lastFiredChatId === chatId) return; // suppress duplicates
    lastFiredChatId = chatId;
    if (window.ReactNativeWebView) {
      window.ReactNativeWebView.postMessage(JSON.stringify({
        type: 'completion',
        chatId: chatId,
        title: title,
        clickUrl: 'atlaschat://c/' + chatId,
      }));
    }
  }

  function check() {
    var chatId = readChatId();
    if (!chatId) return; // not on a chat screen

    var isGenerating = hasStopButton();
    // The stop button is the loudest signal: a transition from
    // present → absent means the turn just ended.
    if (wasGenerating && !isGenerating) {
      postCompletion(chatId, readTitle());
    }
    wasGenerating = isGenerating;

    // Secondary signal: a new .copy-response-button we haven't seen
    // before means a new assistant turn was just completed (covers
    // the rare case where we missed the stop-button transition, e.g.
    // the user was backgrounded when the turn started).
    var btns = countCopyButtons();
    for (var i = 0; i < btns.length; i++) {
      if (!knownCopyButtons.has(btns[i])) {
        knownCopyButtons.add(btns[i]);
        postCompletion(chatId, readTitle());
      }
    }
  }

  // Debounce MutationObserver callbacks so a Svelte re-render that
  // flips several nodes at once collapses into a single check().
  var pending = null;
  function scheduleCheck() {
    if (pending != null) return;
    pending = setTimeout(function() {
      pending = null;
      try { check(); } catch (e) { /* swallow — detector must never crash the page */ }
    }, 150);
  }

  var mo = new MutationObserver(scheduleCheck);
  mo.observe(document.body, {
    childList: true,
    subtree: true,
    attributes: true,
    attributeFilter: ['d', 'class', 'id', 'disabled'],
  });

  // Initial pass after a brief settle — covers the case where the
  // chat was already done when the hook installed (e.g. page reload
  // after a long generation). We deliberately do NOT fire here; the
  // banner's purpose is to notify about NEW completions.
  setTimeout(snapshotKnown, 250);
})();
true;
`;

// ---- pure helpers, exported for unit tests under JSDOM ----

export type CompletionFingerprintInput = {
  pathname: string;
  documentTitle: string;
  /** True if a stop button is currently in the DOM. */
  hasStopButton: boolean;
  /** Number of `.copy-response-button` elements currently in the DOM. */
  copyButtonCount: number;
  /** Snapshot of the previous tick — was a stop button present? */
  wasGenerating: boolean;
  /** Snapshot of the previous tick — known `.copy-response-button` count. */
  knownCopyButtonCount: number;
  /** The chat id we most recently fired a completion for (or null). */
  lastFiredChatId: string | null;
};

/**
 * Read the chat id out of a URL pathname.
 * Returns the UUID-style id, or null if the path doesn't look like
 * `/c/<id>`.
 */
export const chatIdFromPath = (pathname: string): string | null => {
  const m = pathname.match(/^\/c\/([0-9a-fA-F-]{8,})/);
  return m ? m[1] : null;
};

/**
 * Clean up a chat title for the banner. Open WebUI appends
 * `" | Open WebUI"` (or theme-specific text) to the document title in
 * some setups; strip the suffix so the banner stays short.
 */
export const cleanTitle = (raw: string): string => {
  const t = (raw || "").trim();
  const pipe = t.indexOf(" | ");
  const base = pipe > 0 ? t.slice(0, pipe) : t;
  return base || "Chat";
};

/**
 * Pure transition-detection for a single tick. Returns a `CompletionPayload`
 * if the input signals that a new assistant turn just completed, or null
 * if nothing should be posted. The caller is expected to update its
 * `wasGenerating` / `knownCopyButtonCount` / `lastFiredChatId` state
 * based on the return value.
 */
export type CompletionPayload = {
  chatId: string;
  title: string;
  clickUrl: string;
};

export const detectCompletion = (
  i: CompletionFingerprintInput,
): CompletionPayload | null => {
  const chatId = chatIdFromPath(i.pathname);
  if (!chatId) return null;
  if (i.lastFiredChatId === chatId) return null;

  // Primary: stop button present → absent.
  if (i.wasGenerating && !i.hasStopButton) {
    return {
      chatId,
      title: cleanTitle(i.documentTitle),
      clickUrl: `atlaschat://c/${chatId}`,
    };
  }
  // Secondary: a new copy button appeared since the last tick.
  if (i.copyButtonCount > i.knownCopyButtonCount) {
    return {
      chatId,
      title: cleanTitle(i.documentTitle),
      clickUrl: `atlaschat://c/${chatId}`,
    };
  }
  return null;
};
