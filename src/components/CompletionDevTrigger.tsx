// src/components/CompletionDevTrigger.tsx
// DEV-ONLY: a small floating button that fires a fake completion
// into bannerController. Used by Anurag to verify the haptic + banner
// on-device without needing the WebView→native bridge to exist yet
// (the real bridge ships in a follow-up card).
//
// `__DEV__` is true in `expo start` and development builds, false in
// `eas build --profile production` / `preview`. The trigger is
// completely stripped from release builds by Metro's dead-code
// elimination when wrapped in a const-init guard.

import React from "react";
import { Pressable, StyleSheet, Text } from "react-native";
import { useBanner } from "@/notifications/bannerController";

const CompletionDevTriggerInner = () => {
  const show = useBanner((s) => s.show);
  const onPress = () => {
    show({
      chatId: "dev-fake",
      title: "Sample chat (dev trigger)",
      clickUrl: "atlaschat://c/dev-fake",
    });
  };
  return (
    <Pressable
      onPress={onPress}
      style={({ pressed }) => [styles.btn, pressed && styles.pressed]}
      hitSlop={8}
      accessibilityRole="button"
      accessibilityLabel="Fire dev completion (test haptic + banner)"
    >
      <Text style={styles.label}>🔔 test completion</Text>
    </Pressable>
  );
};

// Wrap the whole component in a __DEV__-gated const so the inner
// component reference doesn't even resolve in production builds.
export default __DEV__ ? CompletionDevTriggerInner : () => null;

const styles = StyleSheet.create({
  btn: {
    position: "absolute",
    bottom: 32,
    right: 16,
    backgroundColor: "rgba(34, 211, 238, 0.85)",
    paddingHorizontal: 12,
    paddingVertical: 8,
    borderRadius: 8,
    shadowColor: "#000",
    shadowOpacity: 0.4,
    shadowRadius: 6,
    shadowOffset: { width: 0, height: 2 },
  },
  pressed: { opacity: 0.7 },
  label: { color: "#0F172A", fontSize: 12, fontWeight: "600" },
});
