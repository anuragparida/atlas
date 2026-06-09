// src/notifications/bannerController.ts
// SPEC §7.2 — the in-app completion banner fires only on
// "foregrounded-different-chat". The store action `show` looks at
// AppState + the active conversation id and delegates the decision
// to the pure module `bannerGating.ts` (kept RN-free so the spec's
// three-row table is unit-testable under plain Node).
//
// Backgrounded path: the iOS NTFY app already shows a system
// notification, so we do NOT fire another haptic. Doubling up is
// worse than missing the haptic on the foregrounded path, and the
// spec is explicit about it.

import { AppState, AppStateStatus } from "react-native";
import * as Haptics from "expo-haptics";
import { create } from "zustand";
import { decideGating } from "./bannerGating";

export type CompletionPayload = {
  chatId: string;
  title: string;
  clickUrl: string;
};

type BannerStore = {
  // What's currently visible. null = hidden.
  current: (CompletionPayload & { shownAt: number }) | null;
  // Chat the WebView is currently focused on. SPEC §7.2 says
  // "nothing" when the user is on the chat that finished. The
  // WebView→native bridge will set this on every URL change; until
  // then it stays null and the banner is allowed to fire.
  activeChatId: string | null;
  // App state at the moment show() was last evaluated. We re-evaluate
  // on AppState change so backgrounding mid-banner hides the banner
  // (we never want a banner that survives a backgrounded trip).
  appState: AppStateStatus;
  show: (p: CompletionPayload) => void;
  hide: () => void;
  setActiveChatId: (id: string | null) => void;
  setAppState: (s: AppStateStatus) => void;
};

const AUTO_DISMISS_MS = 8000; // SPEC §10

const useBannerStore = create<BannerStore>((set, get) => ({
  current: null,
  activeChatId: null,
  appState: AppState.currentState ?? "active",

  show: (p) => {
    const { appState, activeChatId, current } = get();
    const decision = decideGating({
      appState,
      activeChatId,
      currentChatId: current ? current.chatId : null,
      incomingChatId: p.chatId,
    });
    if (decision.kind === "suppress") return;

    // SPEC §10 — fire the light haptic on banner-show. The default
    // iOS notification sound is the system NTFY's responsibility; we
    // don't add a second sound here.
    Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light).catch(() => {
      // Haptics can throw on simulators/older devices; swallow.
    });

    set({ current: { ...p, shownAt: Date.now() } });
  },

  hide: () => set({ current: null }),

  setActiveChatId: (id) => {
    set({ activeChatId: id });
    // If the banner is showing the same chat the user just navigated
    // to, dismiss it — they're now looking at the answer.
    const { current } = get();
    if (current && id !== null && current.chatId === id) {
      set({ current: null });
    }
  },

  setAppState: (s) => {
    set({ appState: s });
    // If we just got backgrounded while a banner was up, hide it.
    // Backgrounded completion goes through the iOS NTFY app, not us.
    if (s !== "active") {
      set({ current: null });
    }
  },
}));

// Re-export the hook so React components can subscribe.
export const useBanner = useBannerStore;

// Install the AppState listener once at module load. React Native
// guarantees AppState is a singleton; mounting it here means every
// consumer of `show()` gets the gating for free without each having
// to wire its own listener. The same pattern useReachability uses.
let _installed = false;
export const installBannerController = (): void => {
  if (_installed) return;
  _installed = true;
  const sub = AppState.addEventListener("change", (next) => {
    useBannerStore.getState().setAppState(next);
  });
  // Detach handler is intentionally not stored — this listener lives
  // for the lifetime of the JS process. There is no unmount path
  // (the controller is global, not a React component).
  void sub;
};

export { AUTO_DISMISS_MS };
