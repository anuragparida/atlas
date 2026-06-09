// app/index.tsx
// SPEC §10 — the 3-state wiring. Mounts the WebView when reachable
// (state 1 or 2), shows the VPN banner overlay in state 2, and
// redirects to the full-screen unreachable view in state 3.

import React, { useEffect } from "react";
import { Redirect } from "expo-router";
import { SafeAreaView } from "react-native-safe-area-context";
import { StyleSheet, View } from "react-native";
import { useReachability } from "@/reachability/useReachability";
import { useReachabilityStore, urlForState } from "@/reachability/store";
import ChatWebView from "@/webview/ChatWebView";
import VpnBanner from "@/components/VpnBanner";
import CompletionBanner from "@/components/CompletionBanner";
import CompletionDevTrigger from "@/components/CompletionDevTrigger";
import { installBannerController } from "@/notifications/bannerController";

export default function Index() {
  // The hook owns the probe loop. We just need it mounted once at
  // the root of the chat screen.
  useReachability();

  // SPEC §7.2 — install the AppState listener that gates the
  // completion banner. Idempotent (safe across re-renders / hot
  // reloads). Has to run inside the component body so it executes
  // at least once during the first render pass; the install itself
  // short-circuits after the first call.
  useEffect(() => {
    installBannerController();
  }, []);

  const state = useReachabilityStore((s) => s.state);
  const isHydrated = useReachabilityStore((s) => s.hydrated);

  if (!isHydrated) {
    // First-paint gate — don't flash state 3 if we have a fresh
    // cached lastGood. The hydrate() call resolves quickly (a few ms
    // for AsyncStorage) but we'd rather show nothing than the wrong
    // screen.
    return <View style={styles.container} />;
  }

  if (state === "unreachable") {
    return <Redirect href="/unreachable" />;
  }

  // At this point state is "lan" or "tailscale" (the Redirect above
  // short-circuits "unreachable"), and urlForState maps both to a
  // non-null URL.
  const url = urlForState(state)!;

  return (
    <SafeAreaView style={styles.container} edges={["top", "left", "right"]}>
      {state === "tailscale" && <VpnBanner />}
      {/* SPEC §10 — completion banner overlay. Renders nothing when
          the bannerController has no current completion. Sits above
          the VPN banner so the most recent event wins. */}
      <CompletionBanner />
      <View style={styles.webviewWrap}>
        <ChatWebView url={url} />
      </View>
      {/* DEV-ONLY — dev trigger for verifying haptic + banner on
          device. The component itself is a no-op in release builds. */}
      <CompletionDevTrigger />
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: "#0F172A",
  },
  webviewWrap: { flex: 1 },
});
