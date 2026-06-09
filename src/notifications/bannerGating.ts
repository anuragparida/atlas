// src/notifications/bannerGating.ts
// SPEC §7.2 — pure decision logic for the completion banner.
// Has zero React Native imports so it can be unit-tested under plain
// Node and reasoned about in isolation. The store action
// `bannerController.show` imports this and bails on a `suppress`
// decision before touching AppState or haptics.

// Match React Native's `AppStateStatus` shape so the store can pass
// the value through without an adapter. We only care about the four
// values below; anything else (e.g. "extension" on iPad) is treated
// the same as "unknown" — i.e. we don't fire the banner.
export type GatingInputs = {
  appState:
    | "active"
    | "background"
    | "inactive"
    | "unknown"
    | (string & {}); // forward-compat for future RN AppState values
  activeChatId: string | null;
  currentChatId: string | null; // chat id of the banner currently visible
  incomingChatId: string;
};

export type GatingDecision =
  | { kind: "fire" }
  | { kind: "suppress"; reason: "backgrounded" }
  | { kind: "suppress"; reason: "active-chat" }
  | { kind: "suppress"; reason: "duplicate" };

export const decideGating = (i: GatingInputs): GatingDecision => {
  // SPEC §7.2 row 1: backgrounded → iOS NTFY app handles it. We do
  // nothing. "inactive" is the iOS transition state between active
  // and background; we treat it the same way because the user
  // doesn't have eyes on the WebView either way.
  if (i.appState !== "active") {
    return { kind: "suppress", reason: "backgrounded" };
  }
  // SPEC §7.2 row 2: foregrounded on the chat that finished →
  // "nothing". The user can see the answer in the WebView.
  if (i.activeChatId !== null && i.activeChatId === i.incomingChatId) {
    return { kind: "suppress", reason: "active-chat" };
  }
  // The bridge could re-emit on Open WebUI re-renders. Treat the
  // same chat twice in a row as a single event.
  if (i.currentChatId !== null && i.currentChatId === i.incomingChatId) {
    return { kind: "suppress", reason: "duplicate" };
  }
  return { kind: "fire" };
};
