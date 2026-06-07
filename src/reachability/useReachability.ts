// src/reachability/useReachability.ts
// SPEC §6.3 — the probe loop. Runs only when the app is foregrounded,
// chooses cadence based on the current state, and throttles to once
// per 3s even if multiple triggers fire in the same frame.

import { useEffect, useRef } from "react";
import { AppState, AppStateStatus } from "react-native";
import { useReachabilityStore, ReachabilityState } from "./store";

const UNREACHABLE_INTERVAL_MS = 5_000;
const STABLE_INTERVAL_MS = 30_000;
const THROTTLE_MS = 3_000;

const intervalFor = (s: ReachabilityState) =>
  s === "unreachable" ? UNREACHABLE_INTERVAL_MS : STABLE_INTERVAL_MS;

export function useReachability() {
  const state = useReachabilityStore((s) => s.state);
  const hydrated = useReachabilityStore((s) => s.hydrated);
  const probeNow = useReachabilityStore((s) => s.probeNow);
  const hydrate = useReachabilityStore((s) => s.hydrate);

  // Refs survive re-renders without re-triggering the effect.
  const lastProbeAt = useRef(0);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const runProbe = async (force = false) => {
    const now = Date.now();
    if (!force && now - lastProbeAt.current < THROTTLE_MS) return;
    lastProbeAt.current = now;
    await probeNow();
  };

  const scheduleNext = () => {
    if (timerRef.current) clearTimeout(timerRef.current);
    timerRef.current = setTimeout(async () => {
      await runProbe();
      scheduleNext();
    }, intervalFor(useReachabilityStore.getState().state));
  };

  const cancelTimer = () => {
    if (timerRef.current) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  };

  useEffect(() => {
    // One-time hydration of lastGood + draft from AsyncStorage.
    hydrate();

    const onAppState = (next: AppStateStatus) => {
      if (next === "active") {
        // SPEC: on AppState → active, run probe immediately.
        runProbe(true).then(scheduleNext);
      } else {
        // SPEC: on AppState → background, stop probing entirely.
        cancelTimer();
      }
    };

    // Initial run assumes foregrounded (the hook only mounts while active).
    runProbe(true).then(scheduleNext);

    const sub = AppState.addEventListener("change", onAppState);

    return () => {
      sub.remove();
      cancelTimer();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // When state flips, swap to the appropriate cadence. We don't want
  // to re-run the effect (that would re-subscribe AppState) — just
  // reschedule the timer.
  useEffect(() => {
    if (!hydrated) return;
    scheduleNext();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state, hydrated]);

  return {
    state,
    isHydrated: hydrated,
    forceProbe: () => runProbe(true),
  };
}
