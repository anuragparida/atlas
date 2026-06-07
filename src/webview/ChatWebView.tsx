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
import { WebView, WebViewMessageEvent } from "react-native-webview";
import { useReachabilityStore } from "@/reachability/store";
import {
  TEXT_INPUT_HOOK,
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

  const handleMessage = useCallback(
    (event: WebViewMessageEvent) => {
      const msg = parseBridgeMessage(event.nativeEvent.data);
      if (!msg) return;
      if (msg.type === "unsentDraft" && typeof msg.value === "string") {
        setUnsentDraft(msg.value);
      }
    },
    [setUnsentDraft],
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
      injectedJavaScript={TEXT_INPUT_HOOK}
      onMessage={handleMessage}
      // Some Open WebUI assets assume a modern WebKit build; disable
      // the long-press selection menu on iOS so the chat input stays
      // calm.
      setSupportMultipleWindows={false}
      allowsBackForwardNavigationGestures={false}
      style={{ flex: 1, backgroundColor: "#0F172A" }}
    />
  );
}
