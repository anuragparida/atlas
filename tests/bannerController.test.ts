// tests/bannerController.test.ts
// Node test (no jest/vitest) for the pure gating predicate in
// src/notifications/bannerController.ts. The store action `show()`
// delegates to decideGating, so testing the predicate is the same
// as testing the action's behavior modulo the haptic side effect.
//
// Run with:  node --import tsx --test tests/bannerController.test.ts
// Or:        pnpm tsx --test tests/bannerController.test.ts

import { test } from "node:test";
import assert from "node:assert/strict";
import { decideGating } from "../src/notifications/bannerGating";

const baseInputs = {
  appState: "active" as const,
  activeChatId: "chat-A" as string | null,
  currentChatId: null as string | null,
};

test("fires when foregrounded and on a different chat", () => {
  const d = decideGating({ ...baseInputs, incomingChatId: "chat-B" });
  assert.deepEqual(d, { kind: "fire" });
});

test("suppresses when foregrounded on the chat that finished (spec §7.2: 'nothing')", () => {
  const d = decideGating({ ...baseInputs, incomingChatId: "chat-A" });
  assert.deepEqual(d, { kind: "suppress", reason: "active-chat" });
});

test("suppresses when backgrounded (no double-fire vs iOS NTFY)", () => {
  const d = decideGating({
    ...baseInputs,
    appState: "background",
    incomingChatId: "chat-B",
  });
  assert.deepEqual(d, { kind: "suppress", reason: "backgrounded" });
});

test("suppresses when app state is 'inactive' (transition state, treat like backgrounded)", () => {
  // SPEC §7.2 only enumerates the two end states, but "inactive" is
  // what iOS reports during the active→background transition. The
  // spec's intent is "if the user can't see it, no haptic" so we
  // suppress here too.
  const d = decideGating({
    ...baseInputs,
    appState: "inactive",
    incomingChatId: "chat-B",
  });
  assert.deepEqual(d, { kind: "suppress", reason: "backgrounded" });
});

test("suppresses when the same chat is already showing (duplicate)", () => {
  const d = decideGating({
    ...baseInputs,
    currentChatId: "chat-B",
    incomingChatId: "chat-B",
  });
  assert.deepEqual(d, { kind: "suppress", reason: "duplicate" });
});

test("fires when a different chat finishes while another is showing", () => {
  // New completion supersedes the previous one. (User sees the most
  // recent chat that finished.) The store action replaces `current`
  // when the decision is "fire", so this just needs the predicate
  // to say "fire".
  const d = decideGating({
    ...baseInputs,
    currentChatId: "chat-A",
    incomingChatId: "chat-B",
  });
  assert.deepEqual(d, { kind: "fire" });
});

test("fires when activeChatId is null (bridge not yet wired)", () => {
  // Until the WebView→native bridge ships, the store has no
  // activeChatId. We err on the side of "fire" so a future bridge
  // doesn't have to backfill the active id to start working.
  const d = decideGating({
    ...baseInputs,
    activeChatId: null,
    incomingChatId: "chat-A",
  });
  assert.deepEqual(d, { kind: "fire" });
});

test("active-chat check beats duplicate check (foregrounded + same chat wins over currentChatId)", () => {
  // If the user navigated back to a chat that's already shown in a
  // banner, we want to dismiss via setActiveChatId (which is what
  // actually dismisses the banner in that flow), NOT a fresh fire
  // from decideGating. The active-chat reason is the more specific
  // "nothing" answer.
  const d = decideGating({
    ...baseInputs,
    currentChatId: "chat-A",
    incomingChatId: "chat-A",
  });
  assert.deepEqual(d, { kind: "suppress", reason: "active-chat" });
});
