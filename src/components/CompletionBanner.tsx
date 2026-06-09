// src/components/CompletionBanner.tsx
// SPEC §10 — "Foregrounded-different-chat completion" banner.
//
// Pure presentational component. Reads from the bannerController
// store; renders nothing when there's no current completion. The
// haptic decision is owned by the controller (bannerController.show),
// not this component — keeping the visual and the §7.2 gating logic
// in different files makes the spec's three-row table easy to audit
// without reading JSX.
//
// Mounted at the top of the chat screen (above the WebView) by
// app/index.tsx. Auto-dismisses after 8s, or on tap. Tapping
// deep-links to the conversation via expo-linking.

import React, { useEffect, useRef } from "react";
import { Pressable, StyleSheet, Text, View } from "react-native";
import * as Haptics from "expo-haptics";
import * as Linking from "expo-linking";
import {
  AUTO_DISMISS_MS,
  useBanner,
} from "@/notifications/bannerController";
import { COMPLETION_BANNER_TEXT } from "@/copy/strings";

export default function CompletionBanner() {
  const current = useBanner((s) => s.current);
  const hide = useBanner((s) => s.hide);
  const dismissTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Auto-dismiss after 8s. SPEC §10 — "Auto-dismiss after 8
  // seconds OR on user interaction." We start the timer on mount
  // and clear it on unmount or on early dismiss.
  useEffect(() => {
    if (!current) {
      if (dismissTimer.current) {
        clearTimeout(dismissTimer.current);
        dismissTimer.current = null;
      }
      return;
    }
    dismissTimer.current = setTimeout(() => {
      hide();
    }, AUTO_DISMISS_MS);
    return () => {
      if (dismissTimer.current) {
        clearTimeout(dismissTimer.current);
        dismissTimer.current = null;
      }
    };
  }, [current, hide]);

  if (!current) return null;

  const onTap = () => {
    // Light selection haptic on tap — gives the user feedback that
    // the tap registered before the WebView deep-link kicks in. The
    // show-time haptic is the headline event; this is a small
    // confirmation, separate so it only fires on actual user action.
    Haptics.selectionAsync().catch(() => {});
    // SPEC §10 — "Tap → WebView deep-links to the conversation."
    // We use expo-linking so the existing atlaschat://<path> handler
    // routes the WebView to the conversation. If the URL is invalid
    // (e.g. dev test), we still dismiss the banner so it doesn't
    // linger.
    Linking.openURL(current.clickUrl).catch(() => {});
    hide();
  };

  return (
    <Pressable
      onPress={onTap}
      style={({ pressed }) => [styles.banner, pressed && styles.pressed]}
      hitSlop={8}
      accessibilityRole="button"
      accessibilityLabel={COMPLETION_BANNER_TEXT(current.title)}
    >
      <View style={styles.dot} />
      <Text style={styles.text} numberOfLines={1}>
        {COMPLETION_BANNER_TEXT(current.title)}
      </Text>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  banner: {
    // SPEC §10 — top banner. Same vertical rhythm as the VPN banner
    // (24pt) so the two feel like one design language. The cyan dot
    // is a subtle "new" cue that matches the icon strokes (#22D3EE).
    height: 44,
    backgroundColor: "#0F172A",
    borderBottomColor: "#22D3EE",
    borderBottomWidth: StyleSheet.hairlineWidth,
    flexDirection: "row",
    alignItems: "center",
    paddingHorizontal: 14,
  },
  pressed: { opacity: 0.7 },
  dot: {
    width: 8,
    height: 8,
    borderRadius: 4,
    backgroundColor: "#22D3EE",
    marginRight: 10,
  },
  text: {
    color: "#F8FAFC",
    fontSize: 14,
    fontWeight: "500",
    letterSpacing: 0.2,
    flex: 1,
  },
});
