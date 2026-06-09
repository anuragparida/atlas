// src/components/VpnBanner.tsx
// SPEC §10 — State 2 banner. Thin pill at the top, 24pt tall,
// semi-transparent black, white text with a shield icon. Tapping
// it opens a bottom sheet with the current endpoint and a
// "prefer LAN when available" toggle (default on, in-memory only).

import React, { useState } from "react";
import {
  Modal,
  Pressable,
  StyleSheet,
  Switch,
  Text,
  View,
} from "react-native";
import { TAILSCALE_URL } from "@/reachability/probe";
import { useReachabilityStore } from "@/reachability/store";
import {
  VPN_BANNER_A11Y,
  VPN_BANNER_TEXT,
  VPN_SHEET_TITLE,
} from "@/copy/strings";

const BANNER_HEIGHT = 24;

export default function VpnBanner() {
  const [open, setOpen] = useState(false);
  // SPEC §10 — toggle lives in the Zustand store (in-memory only,
  // intentionally not persisted) so the probe loop can read it.
  const preferLan = useReachabilityStore((s) => s.preferLan);
  const setPreferLan = useReachabilityStore((s) => s.setPreferLan);

  return (
    <>
      <Pressable
        onPress={() => setOpen(true)}
        style={({ pressed }) => [styles.banner, pressed && styles.pressed]}
        hitSlop={8}
        accessibilityRole="button"
        accessibilityLabel={VPN_BANNER_A11Y}
      >
        <Text style={styles.icon}>🛡</Text>
        <Text style={styles.text}>{VPN_BANNER_TEXT}</Text>
      </Pressable>

      <Modal
        animationType="slide"
        transparent
        visible={open}
        onRequestClose={() => setOpen(false)}
      >
        <Pressable
          style={styles.backdrop}
          onPress={() => setOpen(false)}
          accessibilityLabel="Dismiss details"
        />
        <View style={styles.sheet}>
          <View style={styles.handle} />
          <Text style={styles.sheetTitle}>{VPN_SHEET_TITLE}</Text>
          <Text style={styles.endpoint}>{TAILSCALE_URL}</Text>

          <View style={styles.row}>
            <View style={styles.rowText}>
              <Text style={styles.rowLabel}>Prefer LAN when available</Text>
              <Text style={styles.rowSub}>
                Switch back to LAN automatically when home Wi-Fi is reachable.
              </Text>
            </View>
            <Switch
              value={preferLan}
              onValueChange={setPreferLan}
              trackColor={{ false: "#334155", true: "#22D3EE" }}
              thumbColor={preferLan ? "#0F172A" : "#CBD5F5"}
            />
          </View>

          <Pressable
            onPress={() => setOpen(false)}
            style={({ pressed }) => [styles.close, pressed && styles.pressed]}
            accessibilityRole="button"
          >
            <Text style={styles.closeText}>Done</Text>
          </Pressable>
        </View>
      </Modal>
    </>
  );
}

const styles = StyleSheet.create({
  banner: {
    height: BANNER_HEIGHT,
    backgroundColor: "rgba(0,0,0,0.55)",
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    paddingHorizontal: 12,
  },
  pressed: { opacity: 0.7 },
  icon: {
    color: "#F8FAFC",
    fontSize: 12,
    marginRight: 6,
  },
  text: {
    color: "#F8FAFC",
    fontSize: 12,
    fontWeight: "500",
    letterSpacing: 0.2,
  },
  backdrop: {
    flex: 1,
    backgroundColor: "rgba(0,0,0,0.4)",
  },
  sheet: {
    position: "absolute",
    left: 0,
    right: 0,
    bottom: 0,
    backgroundColor: "#0F172A",
    borderTopLeftRadius: 16,
    borderTopRightRadius: 16,
    padding: 20,
    paddingBottom: 32,
  },
  handle: {
    alignSelf: "center",
    width: 40,
    height: 4,
    borderRadius: 2,
    backgroundColor: "#334155",
    marginBottom: 16,
  },
  sheetTitle: {
    color: "#F8FAFC",
    fontSize: 18,
    fontWeight: "600",
    marginBottom: 4,
  },
  endpoint: {
    color: "#94A3B8",
    fontSize: 13,
    fontFamily: "Menlo",
    marginBottom: 24,
  },
  row: {
    flexDirection: "row",
    alignItems: "center",
    paddingVertical: 12,
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: "#1E293B",
  },
  rowText: { flex: 1, paddingRight: 12 },
  rowLabel: { color: "#F8FAFC", fontSize: 15, fontWeight: "500" },
  rowSub: { color: "#94A3B8", fontSize: 12, marginTop: 2 },
  close: {
    marginTop: 24,
    backgroundColor: "#1E293B",
    paddingVertical: 14,
    borderRadius: 12,
    alignItems: "center",
  },
  closeText: { color: "#F8FAFC", fontSize: 16, fontWeight: "600" },
});
