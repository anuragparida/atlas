# Atlas — Mobile Client for Hermes

> Two iOS apps in one project: a thin native shell around Open WebUI for casual daily-driver questions, and (later) a shell around the Hermes WebUI for project management. The first is what we ship now. The second is the same shape, different URL, different bundle ID.

This document is for the agent team. Anurag doesn't read it — agents ask, grill, confirm, and execute.

---

## 1. The two apps, in plain terms

| App | Bundle ID | Target URL | Use case |
|---|---|---|---|
| **Atlas Chat** (ships first) | `com.anuragparida.atlas.chat` | Open WebUI on openclaw:9875 | Casual questions, voice-dumps, dinner/decision/curiosity, anything where the chat UI is the product |
| **Atlas Hermes** (later) | `com.anuragparida.atlas.hermes` | Hermes WebUI on openclaw (the one running this conversation with Anurag) | Project management, kanban, project specs, multi-agent work |

Both share the same Expo codebase with two `app.config.ts` builds. Both reach openclaw over LAN at home, Tailscale when out. Both use the same NTFY listener for completion notifications.

**v1 ships Atlas Chat only.** Atlas Hermes gets a spec stub in §11 and a card in the kanban but no code.

---

## 2. Why this exists

Daily-driver reality: most casual questions, "what should I cook", strategy musings, voice-dump thoughts, etc. happen on the phone. Today those go to ChatGPT because:

- ChatGPT's app is one tap from the home screen
- Voice transcription is invisible (Wispr Flow, OS-level — see §6)
- It just works on any network

The cost: ChatGPT doesn't remember, doesn't know the projects, doesn't know Anurag. Hermes does, after enough conversation, but only if Anurag actually uses it. The mobile UX is the bottleneck.

**Atlas fixes the mobile UX. It does not fix the model, the speed, or the provider — those are separate problems.**

---

## 3. What Atlas is

A native iOS app (single Expo codebase, two build targets):

1. Opens a WebView pointed at Open WebUI running on openclaw
2. Detects which network path reaches openclaw and routes accordingly
3. Survives network transitions (walk out of the house mid-conversation)
4. Receives completion notifications via NTFY (backgrounded) or in-app banner (foregrounded-different-chat)
5. Hides its own reachability probe when not foregrounded

That's it. **No chat components, no model code, no provider routing, no Hermes gateway integration. The chat UI is Open WebUI's job.**

---

## 4. What Atlas is NOT

Not in scope for v1. Each is a future iteration, not a v1 feature.

- ❌ Native chat UI
- ❌ Voice transcription (Wispr Flow at OS level — §6)
- ❌ Multiple LLM provider routing in-app
- ❌ Memory inspection / Honcho tooling in-app
- ❌ Account management, settings beyond network
- ❌ Tablet-optimized layout
- ❌ Offline mode
- ❌ Custom themes
- ❌ Android (deferred — Anurag uses iPhone)
- ❌ Atlas Hermes app (deferred — see §11)

---

## 5. Tech stack

| Layer | Choice | Why |
|---|---|---|
| Framework | **Expo (managed workflow)** | iOS-only target, OTA updates, no Xcode pain, EAS Build for distribution |
| Language | **TypeScript** | Type safety, IDE help, no excuses |
| Routing | **Expo Router** | File-based, the only sensible choice |
| WebView | **`react-native-webview`** | Maintained, supports JS bridges, iOS-polished |
| State | **Zustand** | Reachability state + last-known-good endpoint |
| Persistence | **`@react-native-async-storage/async-storage`** | Last successful endpoint, settings |
| Reachability | **Custom `fetch` with `AbortSignal.timeout(1000)`** to `/health` | No `NetInfo` overhead, no native module to maintain |
| UI | **None.** Status banner hand-rolled, ~30 lines `StyleSheet` | The app is a WebView + a banner. UI libs are the wrong tool. |
| Distribution | **EAS Build → `.ipa` → AltStore** | No Apple Developer $99. See §9. |
| Updates | **EAS Update (OTA)** | Ship reachability fixes without a build round trip |
| Notifications | **NTFY** (self-hosted, not APNS) | No $99 needed. Server-side listener pushes to iPhone. See §7. |

**Why not React Native CLI bare?** No custom native modules needed.

**Why not Capacitor or Tauri?** Both are web-first. Atlas is a WebView host with minimal native chrome — Expo is the right shape.

**Why not PWA with Add to Home Screen?** iOS PWAs drop WebSocket connections in background, can't reliably vibrate on completion, and have weird PWA-only limits. Atlas is the iOS-native experience that happens to render a WebView.

---

## 6. Reachability: the actual hard part

The whole reason this app exists as a native app and not just a bookmarked PWA is that the network path changes and the WebView has to keep up.

### 6.1 The two endpoints

| Name | URL | Works when |
|---|---|---|
| **LAN** | `http://192.168.178.123:9875/` | Phone is on home Wi-Fi (same LAN as openclaw) |
| **Tailscale** | `http://100.83.146.18:9875/` | Tailscale VPN is on |

Both verified working with `curl` from openclaw (LAN: 200, Tailscale IP over HTTP: 200). Both are HTTP, not HTTPS — LAN doesn't need encryption, Tailscale overlay already provides it.

Tailscale HTTPS hostname (`https://openclaw.tail141210.ts.net`) deferred to v2: requires serving Open WebUI on port 443, which collides with Tailscale's daemon. Plain HTTP over the Tailscale interface is fine.

### 6.2 The three states

```
┌──────────────────────────────────────────────────────────┐
│  STATE 1: LAN REACHABLE                                  │
│  → use http://192.168.178.123:9875/                      │
│  → no banner                                             │
└──────────────────────────────────────────────────────────┘
                          │ LAN fails (timeout, non-2xx)
                          ▼
┌──────────────────────────────────────────────────────────┐
│  STATE 2: LAN DOWN, TAILSCALE UP                         │
│  → use http://100.83.146.18:9875/                        │
│  → small persistent banner: "via VPN · shield icon"     │
└──────────────────────────────────────────────────────────┘
                          │ Tailscale also fails
                          ▼
┌──────────────────────────────────────────────────────────┐
│  STATE 3: NEITHER REACHABLE                              │
│  → full-screen "can't reach openclaw" view              │
│  → "Turn on Tailscale" deep link                         │
│  → "Retry" button                                        │
└──────────────────────────────────────────────────────────┘
```

### 6.3 The probe — foregrounded only

**The probe runs only when `AppState === 'active'`.** No background polling. No battery cost when the app is closed. (Anurag confirmed this is the right shape — "if there is no chat that is open or in progress, then it should not be pulling.")

```typescript
// pseudocode
async function probe(url: string): Promise<boolean> {
  try {
    const r = await fetch(url + '/health', { signal: AbortSignal.timeout(1000) });
    return r.ok;
  } catch {
    return false;
  }
}
```

Rules:
- On `AppState` change to `active` → run probe immediately
- On `AppState` change to `background` → stop probing entirely
- Foregrounded cadence: every **5 seconds** while state is uncertain (state 3 → still checking). Every **30 seconds** when stable in state 1 or 2. This is the cost-conscious default; tighten if transitions feel laggy.
- Don't probe more than once per 3s even if you get a stale result
- On probe result change (LAN↔Tailscale), reload the WebView. **Preserve unsent input** in Zustand. iOS WebView drops buffer on URL change; if the user typed something, restore it after reload.

### 6.4 Last-known-good caching

Persist the last successful endpoint + timestamp in AsyncStorage. On cold start, use it as the initial URL **before** the first probe completes. This makes "open app while on the train" feel instant.

If cached endpoint is older than 24h, ignore it and start in state 3 with "checking…" indicator.

---

## 7. Notifications: two paths, one trigger

**The trigger fires server-side.** Server-side is the only reliable option on iOS — background fetch is throttled, APNS needs the $99 developer account we explicitly rejected, and on-device polling dies the moment the app is backgrounded.

### 7.1 The listener service (atlas-notifier, Docker sidecar)

A small Python service that lives in the atlas Docker Compose stack alongside Open WebUI. It:

1. Polls Open WebUI's `/api/v1/chats/?page=1` endpoint every 3 seconds
2. For each chat in `generating` status, records `started_at`
3. When status flips from `generating` → `idle`, fires an NTFY event:
   ```bash
   curl -d "Chat finished: <title>" http://openclaw:8090/atlas-foo
   ```
4. Also subscribes to Open WebUI WebSocket events (preferred, lower latency) — poll is the fallback

**Resource cost:** ~30-50MB RAM, near-zero CPU at idle. ~1% CPU when polling actively. Verified by inspection; will be measured on openclaw once running.

**Storage:** None persistent. All state in-memory.

**The two-URL problem (which server to talk to):** the notifier is just another port on openclaw, like Open WebUI. The app uses the same reachability logic to find it — try LAN first, then Tailscale. The notifier's IP doesn't care which network path the app took. Anurag doesn't think about it.

### 7.2 The two notification paths

| App state | Active conversation? | Behavior |
|---|---|---|
| Backgrounded | doesn't matter | NTFY → iPhone system notification with sound. Tap → open Atlas Chat, deep-link to the conversation. |
| Foregrounded, on the chat that finished | yes | **Nothing.** User can see it. |
| Foregrounded, on a different conversation | no | In-app banner with sound + haptic. **Not** a system notification — Anurag explicitly said that's "a waste of my bandwidth trying to close that notification." |
| Foregrounded, on the home screen of Open WebUI (no chat selected) | no | In-app banner, same as above. |

The WebView injects a small JS listener that watches Open WebUI's completion events and `postMessage`s to the native side. Native decides which path to use based on `AppState` + currently-focused conversation.

### 7.3 NTFY setup

- Self-host NTFY in the atlas Docker Compose stack on openclaw, port 8090
- NTFY's free iOS app is installed on the iPhone, subscribed to topic `atlas-<userid>`
- No Apple involvement, no APNS, no cert pain
- The atlas-notifier service publishes to NTFY; NTFY pushes to the iPhone

---

## 8. App structure

```
atlas/
├── app/                              # Expo Router (or src/app for v2)
│   ├── _layout.tsx                   # Root: WebView + banner overlay
│   ├── index.tsx                     # Main chat screen → renders WebView
│   ├── unreachable.tsx               # State 3 full-screen
│   └── settings.tsx                  # Endpoint overrides (dev)
├── src/
│   ├── reachability/
│   │   ├── probe.ts                  # The fetch-with-timeout primitive
│   │   ├── store.ts                  # Zustand: current state, last-good URL
│   │   └── useReachability.ts        # Hook: probe loop
│   ├── webview/
│   │   ├── ChatWebView.tsx           # The wrapped react-native-webview
│   │   └── messageBridge.ts          # JS↔native: completion events, unsent-text preservation
│   ├── notifications/
│   │   ├── NtfyListener.ts           # NTFY subscription (foregrounded)
│   │   └── BannerController.ts       # In-app banner state
│   └── components/
│       ├── VpnBanner.tsx             # "via VPN" indicator (state 2)
│       └── CompletionBanner.tsx      # "Chat finished" banner (foregrounded-different-chat)
├── app.config.ts                     # Expo config (per-app overrides)
├── eas.json                          # Build profiles
├── tsconfig.json
├── package.json
├── docker/
│   ├── docker-compose.yml            # atlas stack: notifier + ntfy
│   ├── notifier/                     # Python listener service
│   │   ├── Dockerfile
│   │   ├── main.py
│   │   ├── openwebui_client.py
│   │   ├── ntfy_client.py
│   │   └── pyproject.toml
│   └── ntfy/                         # NTFY config
│       └── server.yml
├── assets/
│   ├── icon.png                      # 1024×1024 master
│   ├── icon-ios/                     # iOS app icon set
│   └── splash.png
├── SPEC.md                           # ← this file
├── ROADMAP.md                        # v2/v3 deferred items
└── README.md
```

**Rule:** if a future PR needs a new top-level directory, justify it.

---

## 9. Install path: AltStore (no Apple Developer fee)

**Constraints:**
- No Apple Developer account ($99/yr rejected)
- MacBook is on most days (verified)
- iPhone + MacBook on the same home network regularly

**Path:** Free Apple ID + AltStore + AltServer on the MacBook.

- `eas build --platform ios --profile development` produces a signed `.ipa` (7-day signature)
- AltStore on the iPhone installs the `.ipa`
- AltServer (Mac app, runs on the MacBook) auto-refreshes the signature when iPhone is on the same network
- **If Mac is on at home, this is invisible.** If Mac is off/sleeping when on the road, app expires until re-sign.

Backup: AltStore can be manually re-signed over USB if the MacBook goes down. 30 seconds.

**No App Store. No TestFlight. No $99.** This is the right shape for personal use.

**Build command Anurag runs (we don't run this for them — it costs a build quota slot and 10-20 min):**
```bash
cd atlas
eas login                              # one-time
eas build:configure                    # generates eas.json (one-time)
eas build --platform ios --profile development
# → .ipa, drag into AltStore
```

---

## 10. UX states (visual)

### State 1 — happy path
- App opens → cached URL loads → WebView renders Open WebUI → chat works
- No banner. No chrome. Pure chat.

### State 2 — VPN fallback
- Thin pill at top, 24pt tall, semi-transparent black, white text, shield icon
- Text: "via VPN · tap for details"
- Tap → bottom sheet: current endpoint, "prefer LAN when available" toggle (default on)
- Does not block input. Does not steal focus.

### State 3 — unreachable
- Full-screen, centered, no WebView mounted
- Icon: cloud-with-slash
- Headline: "Can't reach openclaw"
- Sub: "If you're away from home, turn on Tailscale VPN."
- Buttons: "Open Tailscale" (deep link `tailscale://`), "Retry"

### Foregrounded-different-chat completion
- Top banner: "Chat finished: <title> · tap to open"
- Haptic: light impact + sound (default iOS notification sound, low volume)
- Tap → WebView deep-links to the conversation
- Auto-dismiss after 8 seconds OR on user interaction

### Backgrounded completion
- System notification via NTFY: "Chat finished: <title>"
- Default iOS sound
- Tap → opens Atlas Chat, deep-links to the conversation
- Standard iOS notification behavior (swipe to dismiss, etc.)

---

## 11. Atlas Hermes (deferred — Phase 2)

**Same project, different app target.** Will live in the same Expo codebase under `apps/hermes/` or similar, sharing `src/reachability/`, `src/notifications/`, `src/webview/`, and `assets/` with Atlas Chat. Different `app.config.ts` with bundle ID `com.anuragparida.atlas.hermes` and target URL pointing to the Hermes WebUI on openclaw.

**Differences from Atlas Chat:**
- Hermes WebUI has a more complex UI (kanban, project views, agent status, multi-pane layout) → WebView needs more RAM, possibly a desktop-class user-agent string
- More notification triggers (kanban events, agent completions, blocked-item escalations) — same NTFY path, different topics
- Hermes WebUI URL not yet confirmed in spec — will be added in Phase 2

**No code in Phase 1. Just a card in the kanban and a stub spec section.**

---

## 12. Icon design brief

**Concept:** stylized globe on deep navy gradient — a clean circle with two ellipses (longitude lines) and one horizontal line (equator), a subtle compass-rose needle behind it. Reads as "atlas" (maps, navigation, the world).

**Constraints:**
- 1024×1024 master, no transparency, no rounded corners (iOS adds them)
- iOS notification size (29×29) must remain legible
- Single tone + dark background; no busy detail
- Color: deep navy `#0F172A` background, cyan/teal `#22D3EE` for the globe strokes, white `#F8FAFC` for the compass needle
- Also generates: 60×60, 120×120, 180×180, 1024×1024 PNG set for iOS

**Deliverable:** PNG files in `assets/` ready to drop into `app.config.ts` `ios.icon` field. Generated as a one-shot, no design system needed.

---

## 13. Phases & build order

The build order exists to keep Anurag's "working state first, polish later" pattern. We ship the working skeleton early and iterate from real use.

| Phase | Goal | Exit criteria |
|---|---|---|
| **0** | Spec + project skeleton | `atlas/` exists, `SPEC.md` is final, `package.json` is valid, `eas.json` is generated, the project builds on MacBook via `eas build --local` or Expo dev client |
| **1** | WebView + reachability (no notifications) | App installs via AltStore, opens Open WebUI, switches between LAN and Tailscale correctly, state 3 prompt works, no probe when backgrounded |
| **2** | NTFY listener + notifications | `atlas-notifier` Docker service running on openclaw, NTFY publishing, iPhone receives system notifications, foregrounded-different-chat banner works, no notification when on active chat |
| **3** | Polish | Icon shipped, haptics tuned, banner copy finalized, ROADMAP.md captured, README has install steps Anurag can follow from cold |
| **4** | Atlas Hermes app (deferred) | Reuses src/ from Atlas Chat, second `app.config.ts`, second bundle ID, separate AltStore install |

---

## 14. What success looks like

After one week of daily Atlas Chat use:

- ✅ No "Tailscale is off" surprises — the unreachable state is clear and actionable
- ✅ Walking out of the house mid-conversation doesn't drop the chat (or drops it gracefully)
- ✅ The chat UI in Atlas is identical to desktop Open WebUI — because it is
- ✅ App cold-start to first chat render under 1.5s on Wi-Fi
- ✅ Battery cost negligible (no probe when backgrounded)
- ✅ Backgrounded chat completions ping with sound; foregrounded-different-chat gets banner; active chat gets nothing

If all six are true, ship Phase 3, write the README, call Atlas Chat v1 done.
