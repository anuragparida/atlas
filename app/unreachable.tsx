// app/unreachable.tsx
// SPEC §10 — State 3 full-screen. No WebView mounted. Centered
// icon, headline, sub, and two buttons: deep-link to Tailscale and
// a manual Retry that forces an immediate probe.

import React from "react";
import { Linking, Pressable, StyleSheet, Text, View } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { router } from "expo-router";
import { useReachabilityStore } from "@/reachability/store";
import { useReachability } from "@/reachability/useReachability";

export default function Unreachable() {
  // Mount the hook here too so the probe loop keeps running while
  // the user stares at this screen — if openclaw comes back we
  // flip to state 1/2 without a manual retry.
  useReachability();
  const forceProbe = useReachabilityStore((s) => s.probeNow);

  const onRetry = async () => {
    const next = await forceProbe();
    if (next !== "unreachable") router.replace("/");
  };

  return (
    <SafeAreaView style={styles.container} edges={["top", "left", "right", "bottom"]}>
      <View style={styles.center}>
        <Text style={styles.icon}>⚠️🌥️</Text>
        <Text style={styles.headline}>Can't reach openclaw</Text>
        <Text style={styles.sub}>
          If you're away from home, turn on Tailscale VPN.
        </Text>
        <View style={styles.buttons}>
          <Pressable
            onPress={() => Linking.openURL("tailscale://").catch(() => {})}
            style={({ pressed }) => [styles.primary, pressed && styles.pressed]}
            accessibilityRole="button"
          >
            <Text style={styles.primaryText}>Open Tailscale</Text>
          </Pressable>
          <Pressable
            onPress={onRetry}
            style={({ pressed }) => [styles.secondary, pressed && styles.pressed]}
            accessibilityRole="button"
          >
            <Text style={styles.secondaryText}>Retry</Text>
          </Pressable>
        </View>
      </View>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: "#0F172A",
  },
  center: {
    flex: 1,
    alignItems: "center",
    justifyContent: "center",
    padding: 24,
  },
  icon: { fontSize: 56, marginBottom: 24 },
  headline: {
    color: "#F8FAFC",
    fontSize: 22,
    fontWeight: "600",
    marginBottom: 8,
    textAlign: "center",
  },
  sub: {
    color: "#94A3B8",
    fontSize: 15,
    textAlign: "center",
    marginBottom: 32,
    lineHeight: 22,
  },
  buttons: { width: "100%", maxWidth: 320, gap: 12 },
  primary: {
    backgroundColor: "#22D3EE",
    paddingVertical: 14,
    borderRadius: 12,
    alignItems: "center",
  },
  primaryText: { color: "#0F172A", fontSize: 16, fontWeight: "700" },
  secondary: {
    backgroundColor: "#1E293B",
    paddingVertical: 14,
    borderRadius: 12,
    alignItems: "center",
  },
  secondaryText: { color: "#F8FAFC", fontSize: 16, fontWeight: "600" },
  pressed: { opacity: 0.7 },
});
