// tests/openWebUiCompletion.test.ts
// Node test (no jest/vitest) for the pure helpers exported from
// src/notifications/openWebUiCompletion.ts. The production code path
// runs inside the WebView's JS context, so we test the pure parts
// (chatIdFromPath, cleanTitle, detectCompletion) here, plus a smoke
// test that OPEN_WEBUI_COMPLETION_HOOK is a non-empty string with the
// shape we expect (IIFE, double-injection guard, MutationObserver).
//
// Run with:  pnpm tsx --test tests/openWebUiCompletion.test.ts

import { test } from "node:test";
import assert from "node:assert/strict";
import {
  chatIdFromPath,
  cleanTitle,
  detectCompletion,
  OPEN_WEBUI_COMPLETION_HOOK,
} from "../src/notifications/openWebUiCompletion";

// ---------- chatIdFromPath ----------

test("chatIdFromPath extracts the UUID from a /c/<uuid> path", () => {
  assert.equal(
    chatIdFromPath("/c/0d34215f-f709-4c83-8ae1-aa09cd6b196d"),
    "0d34215f-f709-4c83-8ae1-aa09cd6b196d",
  );
});

test("chatIdFromPath returns null on the home page", () => {
  assert.equal(chatIdFromPath("/"), null);
});

test("chatIdFromPath returns null on a settings page", () => {
  assert.equal(chatIdFromPath("/workspace/models"), null);
});

test("chatIdFromPath returns null for /c/ with no id", () => {
  assert.equal(chatIdFromPath("/c/"), null);
});

test("chatIdFromPath returns null for a too-short id", () => {
  // The detector requires at least 8 hex/dash chars to match a real
  // UUID. Anything shorter is almost certainly not a chat id.
  assert.equal(chatIdFromPath("/c/abc"), null);
});

// ---------- cleanTitle ----------

test("cleanTitle strips the '| Open WebUI' suffix", () => {
  assert.equal(cleanTitle("My chat | Open WebUI"), "My chat");
});

test("cleanTitle strips the first pipe", () => {
  // Open WebUI appends "| Open WebUI" or theme-specific text. The
  // first pipe is the boundary we care about.
  assert.equal(cleanTitle("My chat | Hermes Agent WebUI"), "My chat");
});

test("cleanTitle returns 'Chat' for an empty title", () => {
  assert.equal(cleanTitle(""), "Chat");
});

test("cleanTitle returns 'Chat' for a whitespace-only title", () => {
  assert.equal(cleanTitle("   "), "Chat");
});

test("cleanTitle trims surrounding whitespace", () => {
  assert.equal(cleanTitle("  My chat  "), "My chat");
});

test("cleanTitle keeps a title that has no pipe", () => {
  assert.equal(cleanTitle("Just a title"), "Just a title");
});

// ---------- detectCompletion: primary signal ----------

const baseInput = {
  pathname: "/c/0d34215f-f709-4c83-8ae1-aa09cd6b196d",
  documentTitle: "My chat | Open WebUI",
  hasStopButton: false,
  copyButtonCount: 0,
  knownCopyButtonCount: 0,
  lastFiredChatId: null as string | null,
};

test("detectCompletion fires on stop-button transition (present → absent)", () => {
  const out = detectCompletion({
    ...baseInput,
    wasGenerating: true,
    hasStopButton: false,
  });
  assert.ok(out);
  assert.equal(out?.chatId, "0d34215f-f709-4c83-8ae1-aa09cd6b196d");
  assert.equal(out?.title, "My chat");
  assert.equal(out?.clickUrl, "atlaschat://c/0d34215f-f709-4c83-8ae1-aa09cd6b196d");
});

test("detectCompletion does NOT fire when the stop button is still present", () => {
  // The user is still generating; nothing to report.
  const out = detectCompletion({
    ...baseInput,
    wasGenerating: true,
    hasStopButton: true,
  });
  assert.equal(out, null);
});

test("detectCompletion does NOT fire when nothing was generating", () => {
  // No transition.
  const out = detectCompletion({
    ...baseInput,
    wasGenerating: false,
    hasStopButton: false,
  });
  assert.equal(out, null);
});

// ---------- detectCompletion: secondary signal ----------

test("detectCompletion fires on a new copy-response-button appearing", () => {
  // The user backgrounded the app and missed the stop-button
  // transition. The secondary signal — a new copy button for an
  // assistant turn we haven't seen before — still fires.
  const out = detectCompletion({
    ...baseInput,
    wasGenerating: false,
    hasStopButton: false,
    copyButtonCount: 1,
    knownCopyButtonCount: 0,
  });
  assert.ok(out);
  assert.equal(out?.chatId, "0d34215f-f709-4c83-8ae1-aa09cd6b196d");
});

test("detectCompletion does NOT fire when the copy-button count is unchanged", () => {
  // Svelte re-renders can fire MutationObserver for unchanged DOM.
  // We only want to fire when a NEW copy button appears.
  const out = detectCompletion({
    ...baseInput,
    wasGenerating: false,
    hasStopButton: false,
    copyButtonCount: 1,
    knownCopyButtonCount: 1,
  });
  assert.equal(out, null);
});

// ---------- detectCompletion: not on a chat page ----------

test("detectCompletion returns null when the path is not /c/<uuid>", () => {
  const out = detectCompletion({
    ...baseInput,
    pathname: "/",
    wasGenerating: true,
    hasStopButton: false,
  });
  assert.equal(out, null);
});

// ---------- detectCompletion: dedupe ----------

test("detectCompletion suppresses a repeat fire for the same chat", () => {
  // The same chat finishing twice in quick succession (e.g. a
  // regenerate) should fire once. The detector uses lastFiredChatId
  // to dedupe; the production loop maintains that state.
  const out = detectCompletion({
    ...baseInput,
    wasGenerating: true,
    hasStopButton: false,
    lastFiredChatId: "0d34215f-f709-4c83-8ae1-aa09cd6b196d",
  });
  assert.equal(out, null);
});

test("detectCompletion fires for a different chat even if one was fired before", () => {
  const out = detectCompletion({
    ...baseInput,
    pathname: "/c/abcd1234-5678-9abc-def0-1234567890ab",
    wasGenerating: true,
    hasStopButton: false,
    lastFiredChatId: "0d34215f-f709-4c83-8ae1-aa09cd6b196d",
  });
  assert.ok(out);
  assert.equal(out?.chatId, "abcd1234-5678-9abc-def0-1234567890ab");
});

// ---------- OPEN_WEBUI_COMPLETION_HOOK smoke test ----------

test("OPEN_WEBUI_COMPLETION_HOOK is a non-empty string with the expected shape", () => {
  // We don't eval the script here (Node doesn't have a DOM), but
  // we can verify the structural shape: it's an IIFE that returns
  // a literal `true;` (the react-native-webview convention), it
  // guards against double-injection via a window-scoped flag, and
  // it references the stop-button SVG path fragment.
  assert.equal(typeof OPEN_WEBUI_COMPLETION_HOOK, "string");
  assert.ok(OPEN_WEBUI_COMPLETION_HOOK.length > 500);
  assert.ok(OPEN_WEBUI_COMPLETION_HOOK.includes("__atlasCompletionInstalled"));
  assert.ok(OPEN_WEBUI_COMPLETION_HOOK.includes("MutationObserver"));
  assert.ok(OPEN_WEBUI_COMPLETION_HOOK.includes("M2.25 12c0-5.385"));
  assert.ok(OPEN_WEBUI_COMPLETION_HOOK.includes("copy-response-button"));
  assert.ok(OPEN_WEBUI_COMPLETION_HOOK.includes("ReactNativeWebView"));
  assert.ok(OPEN_WEBUI_COMPLETION_HOOK.includes("postMessage"));
  assert.ok(OPEN_WEBUI_COMPLETION_HOOK.trim().endsWith("true;"));
});
