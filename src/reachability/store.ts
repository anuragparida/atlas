// src/reachability/store.ts
// SPEC §6.4 — Zustand store for the reachability state machine,
// last-known-good URL cache, and the unsent-draft buffer the
// WebView uses to survive URL transitions.

import { create } from "zustand";
import AsyncStorage from "@react-native-async-storage/async-storage";
import { LAN_URL, TAILSCALE_URL, probe } from "./probe";

export type ReachabilityState = "lan" | "tailscale" | "unreachable";

export type LastGood = { url: string; ts: number } | null;

const STORAGE_KEY = "@atlas/lastGood";
const STORAGE_KEY_DRAFT = "@atlas/unsentDraft";
const MAX_AGE_MS = 24 * 60 * 60 * 1000; // 24h

export const urlForState = (s: ReachabilityState): string | null => {
  if (s === "lan") return LAN_URL;
  if (s === "tailscale") return TAILSCALE_URL;
  return null;
};

type ReachabilityStore = {
  state: ReachabilityState;
  lastGood: LastGood;
  unsentDraft: string;
  hydrated: boolean;
  /**
   * SPEC §10 — in-memory only. When false, the probe loop skips the
   * LAN probe entirely so the user stays on Tailscale even if LAN
   * becomes reachable. Defaults to true (auto-switch-back enabled).
   * Intentionally NOT persisted to AsyncStorage — it's a per-session
   * preference, and persisting it would surprise users who move
   * between networks.
   */
  preferLan: boolean;
  setState: (s: ReachabilityState) => void;
  setLastGood: (url: string) => void;
  setUnsentDraft: (draft: string) => void;
  setPreferLan: (v: boolean) => void;
  hydrate: () => Promise<void>;
  probeNow: () => Promise<ReachabilityState>;
};

export const useReachabilityStore = create<ReachabilityStore>((set, get) => ({
  state: "unreachable",
  lastGood: null,
  unsentDraft: "",
  hydrated: false,
  preferLan: true,

  // Gate setState on actual change — the useReachability effect
  // listens to `state`, so a no-op transition would otherwise
  // re-schedule the probe timer for no reason.
  setState: (s) => set((prev) => (prev.state === s ? prev : { state: s })),

  setLastGood: (url) => {
    const next: LastGood = { url, ts: Date.now() };
    set({ lastGood: next });
    // Fire-and-forget persistence; AsyncStorage failures don't block UI.
    AsyncStorage.setItem(STORAGE_KEY, JSON.stringify(next)).catch(() => {});
  },

  setUnsentDraft: (draft) => {
    set({ unsentDraft: draft });
    // Note: this fires on every keystroke (300ms debounced at the
    // JS layer in messageBridge). That's one AsyncStorage disk write
    // per draft, which is fine for v1 but worth moving to a
    // background/unmount flush if a profile ever flags the cost.
    AsyncStorage.setItem(STORAGE_KEY_DRAFT, draft).catch(() => {});
  },

  setPreferLan: (v) => set({ preferLan: v }),

  hydrate: async () => {
    try {
      const [raw, rawDraft] = await Promise.all([
        AsyncStorage.getItem(STORAGE_KEY),
        AsyncStorage.getItem(STORAGE_KEY_DRAFT),
      ]);

      let lastGood: LastGood = null;
      if (raw) {
        try {
          const parsed = JSON.parse(raw) as LastGood;
          if (parsed && typeof parsed.ts === "number" && Date.now() - parsed.ts < MAX_AGE_MS) {
            lastGood = parsed;
          }
        } catch {
          // Corrupt cache — treat as missing.
        }
      }

      // If we have a fresh lastGood, seed the state from it so the
      // first render uses the right URL (SPEC §6.4: "open app while
      // on the train should feel instant"). The hook will re-probe
      // and correct it if reality disagrees.
      let initial: ReachabilityState = "unreachable";
      if (lastGood) {
        if (lastGood.url === LAN_URL) initial = "lan";
        else if (lastGood.url === TAILSCALE_URL) initial = "tailscale";
      }

      set({
        lastGood,
        unsentDraft: rawDraft ?? "",
        state: initial,
        hydrated: true,
      });
    } catch {
      set({ hydrated: true });
    }
  },

  probeNow: async () => {
    const { preferLan } = get();
    // SPEC §10 — when preferLan is off, skip the LAN probe entirely
    // and stay on Tailscale even if it would have succeeded. This
    // also covers the "auto-switch-back" case: while preferLan is
    // false we never transition to "lan", so we can't auto-switch
    // away from "tailscale" either.
    if (preferLan) {
      const lanOk = await probe(LAN_URL);
      if (lanOk) {
        get().setState("lan");
        get().setLastGood(LAN_URL);
        return "lan";
      }
    }
    const tsOk = await probe(TAILSCALE_URL);
    if (tsOk) {
      get().setState("tailscale");
      get().setLastGood(TAILSCALE_URL);
      return "tailscale";
    }
    get().setState("unreachable");
    return "unreachable";
  },
}));
