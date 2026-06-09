// src/webview/ChatWebView.tsx
// SPEC §6.3, §10 — wrapped react-native-webview. The component is
// a thin shell that:
//   - reads its URL from props (driven by the reachability store)
//   - on URL change, calls WebView.reload() and re-injects the
//     saved unsent draft via the message bridge
//   - surfaces text-input changes back to the store so the draft
//     survives a network transition
//   - keeps the WebView handle stable across re-renders via useRef
//
// Mounting is owned by the parent (app/index.tsx) so the WebView
// can be torn down entirely when state goes to "unreachable".

import React, { useCallback, useEffect, useRef } from "react";
import {
  WebView,
  WebViewMessageEvent,
  WebViewNavigation,
} from "react-native-webview";
import { useReachabilityStore } from "@/reachability/store";
import { useBanner } from "@/notifications/bannerController";
import {
  TEXT_INPUT_HOOK,
  OPEN_WEBUI_COMPLETION_HOOK,
  buildReinjectScript,
  parseBridgeMessage,
} from "./messageBridge";

type Props = {
  url: string;
};

export default function ChatWebView({ url }: Props) {
  const webViewRef = useRef<WebView | null>(null);
  const lastUrlRef = useRef<string | null>(null);
  const setUnsentDraft = useReachabilityStore((s) => s.setUnsentDraft);
  const unsentDraft = useReachabilityStore((s) => s.unsentDraft);
  const showBanner = useBanner((s) => s.show);
  const setActiveChatId = useBanner((s) => s.setActiveChatId);

  // Extract the chat id from a WebView URL. Open WebUI chat screens
  // live at `/c/<uuid>`; the home and other pages don't match, in
  // which case we return null so the active-chat suppressor doesn't
  // pin to a non-existent chat.
  const extractChatId = useCallback((pageUrl: string): string | null => {
    // Match `…/c/<id>` at the end of the path. Trim query/hash first
    // so `…/c/<id>?…` still matches.
    const path = pageUrl.split(/[?#]/)[0];
    const m = path.match(/\/c\/([0-9a-fA-F-]{8,})/);
    return m ? m[1] : null;
  }, []);

  const handleMessage = useCallback(
    (event: WebViewMessageEvent) => {
      const msg = parseBridgeMessage(event.nativeEvent.data);
      if (!msg) return;
      if (msg.type === "unsentDraft" && typeof msg.value === "string") {
        setUnsentDraft(msg.value);
        return;
      }
      // SPEC §7.2 — a real Open WebUI completion in the WebView
      // arrives as `{type: "completion", chatId, title, clickUrl}`.
      // Route it straight to the banner controller, which decides
      // whether to fire, suppress (active-chat / backgrounded), or
      // dedupe against the previous completion. The detector is in
      // `OPEN_WEBUI_COMPLETION_HOOK` and lives in the WebView.
      // NOTE: we can't use `msg.type === "completion"` to narrow the
      // union because the catch-all `{ type: string; value?: unknown }`
      // arm in `BridgeMessage` matches the same shape. Validate the
      // fields explicitly so the runtime check + the type guard match.
      if (msg.type === "completion") {
        const m = msg as {
          type: string;
          chatId?: unknown;
          title?: unknown;
          clickUrl?: unknown;
        };
        if (typeof m.chatId !== "string" || !m.chatId) return;
        if (typeof m.title !== "string" || !m.title) return;
        if (typeof m.clickUrl !== "string" || !m.clickUrl) return;
        showBanner({
          chatId: m.chatId,
          title: m.title,
          clickUrl: m.clickUrl,
        });
      }
    },
    [setUnsentDraft, showBanner],
  );

  // SPEC §7.2 — on every URL change, sync the banner controller's
  // `activeChatId` so the active-chat-suppresses branch of
  // `decideGating` exercises with real navigation (not just the
  // dev-trigger navigation the Phase 3 card exercised).
  const handleNavigationStateChange = useCallback(
    (event: WebViewNavigation) => {
      const chatId = extractChatId(event.url);
      // null when the user is on the Open WebUI home or a settings
      // page. Setting null is the documented way to clear the
      // suppression so a future completion on a real chat isn't
      // suppressed against a stale id.
      setActiveChatId(chatId);
    },
    [extractChatId, setActiveChatId],
  );

  // Reload the WebView when the URL changes. Per SPEC §6.3: snapshot
  // unsent input (already in the store via the bridge), call reload,
  // and re-inject the saved draft after the new page mounts.
  useEffect(() => {
    if (lastUrlRef.current === null) {
      lastUrlRef.current = url;
      return;
    }
    if (lastUrlRef.current === url) return;
    lastUrlRef.current = url;

    // Snapshot the latest draft before reload — the store already
    // has it, but reload clears the JS state so the bridge will
    // re-emit on remount. We capture the value here so the effect
    // closure has a stable reference for the reinject call.
    const draft = useReachabilityStore.getState().unsentDraft;

    webViewRef.current?.reload();
    // After reload finishes, the new page mounts and the input hook
    // runs (TEXT_INPUT_HOOK). We then re-inject the saved draft.
    const t = setTimeout(() => {
      const script = buildReinjectScript(draft);
      webViewRef.current?.injectJavaScript(script);
    }, 800);

    return () => clearTimeout(t);
  }, [url]);

  // Re-inject on first mount if we hydrated with a saved draft.
  useEffect(() => {
    if (lastUrlRef.current !== null) return; // only on first mount
    if (!unsentDraft) return;
    const t = setTimeout(() => {
      webViewRef.current?.injectJavaScript(buildReinjectScript(unsentDraft));
    }, 1200);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <WebView
      ref={webViewRef}
      source={{ uri: url }}
      originWhitelist={["*"]}
      javaScriptEnabled
      domStorageEnabled
      // Two scripts: (1) the unsent-draft bridge, (2) the
      // completion detector. Both are idempotent — they guard
      // against double-injection via a window-scoped flag, so
      // concatenating them into a single injectedJavaScript
      // string is safe.
      injectedJavaScript={`${TEXT_INPUT_HOOK}\n${OPEN_WEBUI_COMPLETION_HOOK}`}
      onMessage={handleMessage}
      onNavigationStateChange={handleNavigationStateChange}
      // Some Open WebUI assets assume a modern WebKit build; disable
      // the long-press selection menu on iOS so the chat input stays
      // calm.
      setSupportMultipleWindows={false}
      allowsBackForwardNavigationGestures={false}
      style={{ flex: 1, backgroundColor: "#0F172A" }}
    />
  );
}
