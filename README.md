# Atlas

Native iOS app: a WebView around Open WebUI, with reachability (LAN ↔ Tailscale) and NTFY notifications. Ships as one iOS app (`Atlas Chat`) today; second app (`Atlas Hermes`) deferred to Phase 4.

Design lives in `SPEC.md`. Phase 2 (the `atlas-notifier` service and NTFY Docker stack) is spec-only in `PHASE2-SPEC.md` — no code for it yet. This file is the install + day-2 runbook for the **as-shipped** scaffold.

---

## 1. What's in the box

Expo + TypeScript iOS project, single bundle, two routes:

| File / dir | Purpose |
|---|---|
| `app/_layout.tsx` | Root `<Stack>`, dark background (`#0F172A`), no header |
| `app/index.tsx` | The chat screen — gates a `<ChatWebView>` on reachability. Also mounts `useReachability()` so the probe loop is alive here. |
| `app/unreachable.tsx` | Full-screen "Can't reach openclaw" with Open Tailscale + Retry buttons |
| `app.config.ts` | `name: "Atlas Chat"`, `bundleIdentifier: com.anuragparida.atlas.chat`, ATS allows local + arbitrary, `newArchEnabled: true` |
| `eas.json` | `development` + `preview` profiles. **No `production` profile** — that needs a paid Apple Developer account. |
| `tsconfig.json` | Extends `expo/tsconfig.base`, `strict: true`, `@/*` → `src/*` |
| `src/reachability/probe.ts` | Exports `LAN_URL` (`http://192.168.178.123:9875`) and `TAILSCALE_URL` (`http://100.83.146.18:9875`). One-second `fetch` to `/health` with `AbortSignal.timeout(1000)`. **The URLs live here, not in `app.config.ts`.** |
| `src/reachability/store.ts` | Zustand store. State machine (`lan` / `tailscale` / `unreachable`), `lastGood` cache (24h TTL), `unsentDraft` buffer, persisted via AsyncStorage. |
| `src/reachability/useReachability.ts` | The probe loop. 5s cadence when unreachable, 30s when stable, 3s throttle, AppState-aware (stops on background). |
| `src/webview/ChatWebView.tsx` | `<WebView>` shell. Reloads on URL change, re-injects the saved draft on first paint and after every reload, keeps a stable ref. |
| `src/webview/messageBridge.ts` | JS↔native bridge. Injected `TEXT_INPUT_HOOK` watches input/keyup/focusin with 300ms debounce; `buildReinjectScript` restores draft via the native value-setter. |
| `src/components/VpnBanner.tsx` | 24pt pill rendered in state 2. Tap opens a bottom sheet with the current Tailscale URL and a "Prefer LAN when available" toggle (in-memory, not persisted). |
| `assets/icon.png` + `assets/icon-ios/` (9 PNGs) | App icon set, all sizes including 1024 |
| `src/notifications/` | Empty placeholder, will hold the notifier glue in Phase 2 |

v1 = `Atlas Chat` (`com.anuragparida.atlas.chat`) → `http://192.168.178.123:9875/` (LAN) or `http://100.83.146.18:9875/` (Tailscale).

No native chat UI. No provider routing. No memory tools. The chat UI is Open WebUI's job.

---

## 2. Prerequisites

- **MacBook on most days.** AltServer runs on it; without it, 7-day signatures expire and Atlas stops launching.
- **iPhone** with a free Apple ID signed into iCloud (no Apple Developer $99/yr needed).
- **AltStore** installed on the iPhone.
- **AltServer** installed and running on the MacBook (system tray).
- **Tailscale** installed on the iPhone and on `openclaw`. Logged in.
- **openclaw running**, reachable at `http://192.168.178.123:9875/` (LAN) and `http://100.83.146.18:9875/` (Tailscale).
- **Open WebUI** is up on openclaw port 9875 (`docker compose ps` on openclaw should show it healthy).
- **Node 18+ and pnpm** on the MacBook for the Expo CLI / EAS CLI / `pnpm install`.
- **Expo account** (free, `https://expo.dev/signup`).

Verify the openclaw side from the MacBook terminal before doing anything else:

```bash
curl -fsS http://192.168.178.123:9875/health    # expect 200
curl -fsS http://100.83.146.18:9875/health      # expect 200
```

If either fails, fix openclaw first. Don't try to build the app until both probes return 200.

---

## 3. One-time setup (per machine)

```bash
# install the EAS CLI globally
npm install -g eas-cli

# log in (browser pops up, use the free Expo account)
eas login

# install JS deps for the scaffold
cd atlas
pnpm install
```

The `eas.json` and `app.config.ts` are already committed. **Do not run `eas build:configure`** — it would overwrite the committed build profiles and force you to re-pin `development` as the default. If `eas build` complains that something is missing, the file got clobbered; check `git diff` and revert.

Sanity-check the scaffold before building:

```bash
pnpm typecheck    # tsc --noEmit, should report 0 errors
```

---

## 4. Build + install

Each build costs an EAS build quota slot and ~10–20 minutes. Don't burn one unless §5 is going to pass.

```bash
cd atlas
eas build --platform ios --profile development
```

EAS prints a URL when the build is done. Open it, download the `.ipa`.

Install on the iPhone:

1. Plug iPhone into the MacBook (or AirDrop the `.ipa` to the iPhone).
2. AltStore on the iPhone: tap **My Apps → +** in the top-left, pick the `.ipa`.
3. Trust the developer profile: iOS **Settings → General → VPN & Device Management → [your Apple ID] → Trust**.

That's the install. Cold-start the app. It should open Open WebUI within ~1.5s on Wi-Fi.

---

## 5. First-launch checklist

In order. Stop at the first failure.

1. **App opens to Open WebUI on home Wi-Fi.** If you see "Can't reach openclaw", check `curl http://192.168.178.123:9875/health` from the MacBook. If the MacBook can reach it but the iPhone can't, they're on different VLANs — fix the network before going further.
2. **Sign in to Open WebUI** (regular Open WebUI auth). Send a one-word message. Get a one-word reply. Confirms the WebView is wired and not just rendering cached HTML.
3. **Walk out of Wi-Fi range** (or toggle Wi-Fi off on the iPhone). Turn on Tailscale. The pill at the top should flip to "via VPN · shield icon". The chat should keep working without a reload flicker (or with one, then continue). If the app jumps straight to "Can't reach openclaw" while Tailscale is on, verify `curl http://100.83.146.18:9875/health` from the iPhone's Tailscale network.
4. **Lock the iPhone for a minute, unlock, see a fresh probe.** The probe runs in 5s cadence while unreachable and 30s when stable; it should pause entirely in background. If the pill state ever looks wrong, force-quit and cold-start to reseed from AsyncStorage.
5. **Type into the chat, lock the iPhone immediately, unlock.** Whatever you typed should still be in the input box. The Zustand `unsentDraft` survives WebView reloads and is re-injected by `ChatWebView`.
6. **No probe in background.** Send a message, lock the iPhone, leave it for an hour. Battery delta in iOS Settings should be flat (or within noise). If it's draining, the probe is leaking.

The first three are the must-pass for v1. Items 4–6 confirm the v1-quality work; they're not strictly required for day-1 ship.

---

## 6. 7-day signature expiry

Free Apple IDs sign apps for **7 days**. After that, Atlas refuses to launch: black screen, "this app is no longer available", or instant crash on open.

**Auto-refresh (the normal case):** AltServer on the MacBook + iPhone on the same Wi-Fi network re-signs the app silently in the background. Don't lock the MacBook with the lid closed overnight — it needs to be awake on the same network as the iPhone.

**Manual refresh (Mac went down, you're on the road):**

```bash
# on the MacBook, after plugging the iPhone in via USB
open -a AltStore
# My Apps → atlas → Refresh
```

30 seconds.

**If the app refuses to launch after 7+ days and you can't get to the MacBook**, re-install the `.ipa` from the EAS build URL the same way you did the first time. The new install is fresh-signed for another 7 days.

Treat the 7-day clock as the price of skipping the $99. The trade is fine for daily-driver personal use; it's not fine for an app you hand to other people.

---

## 7. NTFY install on iPhone

NTFY is how the server-side listener pushes completion events to the iPhone when Atlas is backgrounded. No APNS, no $99, no certs.

**This is wired in spec only.** `PHASE2-SPEC.md` covers the `atlas-notifier` service that publishes to NTFY, and the NTFY Docker stack that runs on openclaw port 8090. Neither is built yet — when they are, this section becomes the end-to-end test.

Once Phase 2 ships:

1. App Store → search **"ntfy"** → install the official NTFY app (by Philipp Heckel, the project's author; free, no IAP).
2. Open the app. Tap **+** to add a subscription.
3. **Server URL:** `http://100.83.146.18:8090` (Tailscale, since NTFY is openclaw-side, not on the public internet).
   - LAN alternative: `http://192.168.178.123:8090` if the iPhone is on home Wi-Fi.
   - If you later add TLS or a public hostname, change this here and re-test.
4. **Topic:** `atlas-anurag`. Lowercase, no spaces, no leading slash. (The atlas-notifier service publishes to this exact topic; mismatch = silent failure.)
5. **Auth:** none for v1. NTFY on openclaw is bound to Tailscale + LAN, not exposed to the public internet. If you ever expose it, add a token and put it in NTFY's per-subscription settings.
6. Tap **Subscribe**. The test notification fires immediately. You should see "Test message" in the iPhone notification tray.

Sanity check the end-to-end path:

```bash
# from the MacBook, or anywhere that can reach openclaw
curl -d "smoke test from CLI" http://100.83.146.18:8090/atlas-anurag
```

The iPhone should buzz within a second. If it doesn't, the topic or URL is wrong, or the atlas-notifier service isn't running on openclaw. Don't ship until this round-trips.

---

## 8. Troubleshooting

Work top-down. Each row assumes you've already failed the row above.

| Symptom | First check | Then |
|---|---|---|
| `eas build` fails on login | `eas whoami` | `eas logout && eas login`; verify the Expo account is the free one, not a paused org |
| Build fails: "No bundle identifier" | `app.config.ts` has `ios.bundleIdentifier` | You ran `eas build:configure`; revert `app.config.ts` and `eas.json` from git |
| Build fails: "No provisioning profile" (free Apple ID) | This is normal for `production` profile | Use `--profile development` (default above). `production` requires a paid Apple Developer account and isn't in `eas.json` on purpose. |
| `pnpm typecheck` reports errors in `node_modules` | Stale install | `rm -rf node_modules pnpm-lock.yaml && pnpm install` |
| App installs, opens, black screen, then closes | 7-day signature expired | Re-install from the latest `.ipa`; see §6 |
| App installs, opens, "Can't reach openclaw" | `curl http://192.168.178.123:9875/health` from the MacBook | If that fails: openclaw is down, restart the stack. If it passes: iPhone isn't on the same LAN; either join home Wi-Fi or turn on Tailscale. |
| App shows "via VPN" but on home Wi-Fi | Tailscale is on and the LAN probe tripped its 1s timeout (iPhone on a guest VLAN, AP isolation, or openclaw is down on the LAN). Tailscale is the fallback, not the faster probe. | Not a bug. The pill is informational. Toggling Tailscale off reverts within ~30s (one stable-state probe tick). |
| LAN fine, Tailscale fine, but app still says "Can't reach" | Wrong URL baked into `src/reachability/probe.ts` | Confirm `LAN_URL` and `TAILSCALE_URL` match your openclaw's actual addresses. **This is where the URLs live, not `app.config.ts` and not `.env`.** Rebuild. |
| State pill flips during a single conversation | Probe reload is racing the WebView | Cosmetic. The `unsentDraft` is preserved; reload + re-inject should not lose typed text. If it does, file a card — that's a real bug. |
| App drains battery | Probe running while backgrounded | `useReachability` registers an `AppState` listener and `cancelTimer()`s on `background`. If you added a second probe, that's the leak. |
| Chat cuts off mid-message when walking between rooms | Probe reloaded the WebView before the new network path was confirmed stable | Cosmetic in v1. The unsent-input preservation in Zustand is wired; if the message was already sent, it'll show up after the reload. |
| `eas build` works but iPhone says "unable to install" | iPhone trust prompt never tapped | iOS Settings → General → VPN & Device Management → trust the Apple ID used in `eas login` |
| AltStore says "Could not connect to AltServer" | MacBook is asleep or on a different network | Wake the MacBook, confirm same Wi-Fi as the iPhone. AltServer must be running. |

If the table doesn't cover it, the spec has the design intent: `SPEC.md` §6 (reachability), §7 (notifications), §9 (install path). Read the section, then re-check what you observed.

---

## 9. Day-2 changes you'll probably make

Common tweaks, with the file you actually have to touch:

| Change | File | Notes |
|---|---|---|
| Switch to a different openclaw host (LAN / Tailscale) | `src/reachability/probe.ts` | Edit `LAN_URL` and `TAILSCALE_URL`. Rebuild required — these are constants, not config. |
| Change probe cadence (5s / 30s / 3s throttle) | `src/reachability/useReachability.ts` | `UNREACHABLE_INTERVAL_MS`, `STABLE_INTERVAL_MS`, `THROTTLE_MS` at the top. OTA-updateable. |
| Persist the "Prefer LAN when available" toggle | `src/components/VpnBanner.tsx` + `src/reachability/store.ts` | Currently in-memory only. Add an `AsyncStorage` round-trip. |
| Change the dark theme color | `app/_layout.tsx` + `app/index.tsx` + `app/unreachable.tsx` | `#0F172A` is the slate-900 base. Search-and-replace carefully — it's also the WebView background to avoid a flash. |
| Add a new build profile (e.g. `production` for TestFlight) | `eas.json` | Requires a paid Apple Developer account. Not in scope for v1. |
| Add a new screen | `app/<screen>.tsx` | Expo Router auto-routes. `app/_layout.tsx` controls headers. |

When in doubt, the entry point is `app/index.tsx` — it owns the reachability hook and the WebView mount.

---

## 10. What's not in v1 (yet)

- **Atlas Hermes app.** Same scaffold, different bundle id (`com.anuragparida.atlas.hermes`), different URL, different name. See `ROADMAP.md` §1.
- **atlas-notifier service + NTFY Docker stack.** **Shipped (Phase 2, 2026-06-08).** Spec is in `PHASE2-SPEC.md`; the `docker/` subtree holds the live code. End-to-end notification delivery now works; the `§5` checks above are the as-shipped verification.
- **EAS Update (OTA).** The scaffold is set up for it (`cli.appVersionSource: "remote"`, OTA-updateable JS), but no update channel is configured. Add an `update` block to `eas.json` and run `eas update:configure` when you're ready to ship JS-only fixes without a build round trip. See `ROADMAP.md` §9.
- **Android.** The scaffold has the Android config block (`package: com.anuragparida.atlas.chat`) but no iOS-spec-required Android-specific testing. Deferred — see `SPEC.md` §4 and `ROADMAP.md` §2.

For the full deferred-items list (Atlas Hermes, multi-user NTFY, custom themes, tablet layout, offline mode, Tailscale HTTPS, EAS production/TestFlight, EAS Update, in-app memory tooling), see **`ROADMAP.md`**. Each item has a one-line description, an estimated scope (weekend / phase / full-project), and the open decision points.

---

## One-line summary

MacBook + iPhone + free Apple ID + AltStore + Tailscale + openclaw on 9875 + NTFY on 8090 → `pnpm install && eas build --platform ios --profile development` → drag `.ipa` into AltStore → ship.
