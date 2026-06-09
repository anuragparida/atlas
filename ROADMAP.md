# Atlas — ROADMAP

> Source of truth for what is **not** in v1 of Atlas Chat. Captured per `t_930405ff`
> so the next session doesn't re-derive scope from `SPEC.md` §4. Each item carries
> three fields: **what it is**, **estimated scope**, and the **decision points**
> that have to be resolved before the work starts.
>
> Anchor spec sections: `SPEC.md` §4 ("What Atlas is NOT"), §11 (Atlas Hermes stub),
> §7 (NTFY), §6 (reachability).

## Scope legend

| Tag | Meaning |
|---|---|
| `weekend` | One focused weekend. No new infra, no new service, no schema migration. |
| `phase` | A real phase (1–2 weeks): new service, new install step, schema change, or a sibling app. |
| `full-project` | Multi-week, multi-decision. Treat as a separate project; do not bundle into a phase. |

## Items

### 1. Atlas Hermes app — `phase`

- **What:** Second iOS app in the same Expo codebase. Different bundle ID
  (`com.anuragparida.atlas.hermes`), different name (`Atlas Hermes`), different
  target URL (Hermes WebUI on openclaw, URL TBD). Reuses `src/reachability/`,
  `src/notifications/`, `src/webview/`, `assets/`. Lives under `apps/hermes/` or
  a second `app.config.ts` + EAS build profile.
- **Spec ref:** `SPEC.md` §11. Currently marked deferred-to-Phase-2, but Phase 2
  became the notifier work — Hermes is now a real future phase.
- **Scope:** `phase` — one bundle, one AltStore install, no new infra. Mostly
  config (bundle id, name, URL constant) and a Hermes-specific notification
  topic set.
- **Decision points:**
  - Hermes WebUI URL not yet specified — confirm before building (port? path
    prefix? sub-route?).
  - Hermes WebUI has a heavier UI (kanban, multi-pane) — confirm whether
    WebView needs a desktop-class user-agent string or extra RAM, or if the
    default mobile UA is fine.
  - Hermes notification surface is bigger (kanban events, agent completions,
    blocked-item escalations). Confirm which triggers map to which NTFY topics.
  - Does the Hermes app share the iOS NTFY app subscription, or does it need
    a separate one with topic prefix `hermes-<userid>`?

### 2. Android client — `weekend` (deferred indefinitely)

- **What:** Run Atlas Chat on Android. The Expo scaffold already carries the
  Android config block (`package: com.anuragparida.atlas.chat`), but no
  Android-spec testing was done.
- **Spec ref:** `SPEC.md` §4.
- **Scope:** `weekend` *if* Anurag ever cares about Android. Otherwise stays
  deferred. The Expo build is one `eas build --platform android` away; the
  install story is the same AltStore-equivalent (F-Droid + Obtainium, or
  sideload). Push notifications route through FCM instead of NTFY's iOS app
  — that's the real work.
- **Decision points:**
  - Is the iOS NTFY app → NTFY Android app swap good enough, or do we want
    FCM/APNS-style native push on Android?
  - Does Anurag ever want this, or is the iPhone the long-term personal
    device? If never, mark as `cancelled` and stop carrying the Android
    config block.

### 3. Multi-user NTFY — `phase`

- **What:** v1 is single-tenant — one NTFY topic per Open WebUI user, owned by
  the operator. Multi-user means per-user topics (`atlas-<userid>` already
  exists) **and** per-user ACLs (per-user publish/subscribe tokens), with the
  notifier service authenticating as the right principal for each user. The
  current `binwiederhier/ntfy` ACL model is shared-secret per topic; per-user
  tokens are a config + bootstrap change.
- **Spec ref:** `SPEC.md` §7.3, `PHASE2-SPEC.md` §1.4.
- **Scope:** `phase` — touches the NTFY ACL config, the token bootstrap
  script, the notifier's auth header, and the iOS NTFY app's per-topic
  credentials.
- **Decision points:**
  - Per-user publish tokens (notifier → NTFY) or per-user subscribe tokens
    (iOS → NTFY) — both, or just one side?
  - Where do per-user tokens live? Open WebUI DB? A sidecar `tokens.yaml`?
    Vault?
  - Does the iOS NTFY app get one credential set per topic, or a single
    user-level credential that grants access to all topics the user owns?

### 4. Custom themes — `weekend`

- **What:** User-selectable color schemes in Atlas Chat. v1 is a single dark
  theme (`#0F172A` slate-900 base, `#22D3EE` cyan accent). The same colors
  are hardcoded in `app/_layout.tsx`, `app/index.tsx`, `app/unreachable.tsx`,
  and the WebView background.
- **Spec ref:** `SPEC.md` §4.
- **Scope:** `weekend` — move the palette to a `src/theme/` module, add a
  picker in the unreachable screen or a long-press on the pill, persist via
  AsyncStorage. The WebView background is the only non-trivial piece — has
  to be re-injected on theme change to avoid the white flash.
- **Decision points:**
  - How many themes ship with v2? (Light + dark is the minimum; a system-
    follows-mode toggle is the maximum.)
  - Is the theme global per device, or per Atlas app instance? (Hermes app
    will want its own theme — confirm whether `src/theme/` is shared.)
  - Does the Open WebUI `<body>` background also need a matching theme
    re-inject, or is the WebView's `backgroundColor` the only knob that
    matters for the white-flash fix?

### 5. Tablet-optimized layout — `weekend` (deferred)

- **What:** iPad layout — split view, sidebar, larger touch targets. v1 is
  single-column, phone-shaped; on an iPad the WebView fills the canvas but
  the chat list (if any) and the unreachability UI don't reflow.
- **Spec ref:** `SPEC.md` §4.
- **Scope:** `weekend` for a usable split-view; `phase` if we want
  pointer-optimized interactions (hover states, right-click menus). React
  Native's `useWindowDimensions()` + a two-pane layout gets the first cut.
- **Decision points:**
  - iPad only, or also Android tablets (depends on item #2)?
  - Does the WebView stay full-width, or does Atlas render a native
    sidebar (chat history, settings) alongside the WebView?
  - Multi-task / Split View on iPad — does the probe loop need to pause
    when the app is half-width? Reachability logic was written for
    full-foreground, full-background.

### 6. Offline mode — `phase`

- **What:** Atlas Chat works without an openclaw connection. Today the
  unreachability screen is terminal: "Can't reach openclaw, Open Tailscale,
  Retry." Offline means: read-only access to the last cached conversation
  (WebView local storage survives), a queue for outbound messages, and a
  sync on reconnect.
- **Spec ref:** `SPEC.md` §4.
- **Scope:** `phase` — a service worker in the WebView for localStorage
  sync, a native message buffer in Zustand, and a queue-flush protocol on
  reconnect that doesn't lose the user's draft or duplicate sends.
- **Decision points:**
  - Is offline mode "read what you have" or "queue and send later"? Both
    is a real phase; just-read is a weekend.
  - Does the notifier queue too, or is offline = "no notifications, period"?
  - How long does the WebView's localStorage survive a reinstall? (iOS
    wipes it on app uninstall — confirm we don't rely on it for the
    draft buffer.)

### 7. Tailscale HTTPS hostname — `full-project`

- **What:** Serve Atlas over `https://openclaw.tail141210.ts.net` so the
  iPhone (and any browser) hits it on a stable, shareable URL. Today the
  URLs are plain HTTP: `http://192.168.178.123:9875` (LAN) and
  `http://100.83.146.18:9875` (Tailscale).
- **Spec ref:** `SPEC.md` §6, called out explicitly in the spec as
  deferred-to-v2.
- **Scope:** `full-project` — the headline blocker is that HTTPS means
  serving Open WebUI on port 443, which **collides with the Tailscale
  daemon on openclaw**. Resolving that is an infra decision, not a config
  tweak. Options: move Open WebUI behind a Caddy/nginx reverse proxy on
  a high port, run Tailscale on a different port, or use a Funnel-style
  relay. Each is a multi-step ops change.
- **Decision points:**
  - Port collision — resolve first. Pick: move Tailscale daemon to a
    non-443 port (Tailsscale allows this but it's a global openclaw
    change), or run a reverse proxy in front of Open WebUI, or use
    Tailscale Funnel (which gives a `*.ts.net` URL without the port
    issue but has its own ACL implications).
  - TLS cert source — Tailscale's built-in cert, Let's Encrypt via
    `tailscale cert`, or self-signed with the iOS app trusting the
    profile?
  - App Transport Security (ATS) — the v1 `app.config.ts` allows
    arbitrary loads; once we have HTTPS we can tighten that, but the
    WebView's behavior under mixed-content rules needs re-checking.
  - Once `https://openclaw.tail141210.ts.net` is live, do the LAN and
    Tailscale-plain-HTTP URLs still exist, or does Atlas only ever hit
    the HTTPS one (LAN + remote both)?

### 8. EAS production profile / TestFlight — `weekend`

- **What:** Add a `production` profile to `eas.json` so Atlas can ship via
  TestFlight (and eventually App Store). v1 only has `development` and
  `preview` because `production` needs a paid Apple Developer account
  ($99/yr).
- **Spec ref:** `README.md` §9 (Day-2 changes).
- **Scope:** `weekend` *after* the Apple Developer fee is paid — the
  `eas.json` change is one block, the rest is the App Store Connect
  metadata. Not worth a phase on its own; not worth doing before the
  account exists.
- **Decision points:**
  - Is Anurag going to pay the $99/yr Apple Developer fee, or is
    AltStore + free Apple ID the long-term install path?
  - If yes: do we need a privacy policy URL and a support URL before
    the first TestFlight build?

### 9. EAS Update (OTA JS) — `weekend`

- **What:** Wire up OTA JS updates so a config change in
  `src/reachability/probe.ts` (URLs, probe cadence) can ship without a
  full rebuild round trip. The scaffold is already set up for it
  (`cli.appVersionSource: "remote"` in `eas.json`).
- **Spec ref:** `README.md` §10.
- **Scope:** `weekend` — add an `update` block to `eas.json`, run
  `eas update:configure`, document the `eas update` CLI in §6 of the
  README. Native code changes still need a full build; this is JS-only.
- **Decision points:**
  - Which channel (`default`, `staging`, `production`) ships where?
  - Do we want a manual gate (Anurag runs `eas update` by hand) or a
    push-on-main workflow?

### 10. Open WebUI memory / Honcho tooling in-app — `full-project`

- **What:** Surface Open WebUI's memory and Honcho integration in the
  Atlas WebView. Today, if the user wants to inspect or edit memory, they
  open the WebUI and navigate to the memory page manually. v1 explicitly
  defers this; v2 might add a deep-link from Atlas to the memory page, or
  a native sidebar.
- **Spec ref:** `SPEC.md` §4.
- **Scope:** `full-project` if we build a native sidebar; `weekend` if
  we just add a "Manage memory" deep-link in the unreachable screen's
  settings sheet. The "full" version depends on whether Honcho exposes
  the same surface Open WebUI does.
- **Decision points:**
  - Native sidebar (real product work) or a deep-link to the WebUI's
    memory page (configuration)?
  - Does the user need read-only, or read-write? Read-write means
    Atlas owns the auth handshake for the WebView session.

## Items not on this list (and why)

- **Native chat UI.** Out of scope for Atlas as a product — the whole
  point is "the WebView is the chat UI." If a native chat UI is wanted,
  that's a different app, not Atlas.
- **Multiple LLM provider routing in-app.** Open WebUI's job. Atlas
  doesn't add a second router.
- **Account management, settings beyond network.** Same — Open WebUI's
  job. Atlas owns reachability + notification delivery; nothing else.

## When an item moves from ROADMAP to active

- An item leaves this list when a kanban card is created against it
  (assignee = perseus for build, athena for spec, helena for review).
- The kanban card's body should link back to the ROADMAP item by
  section number (e.g. "Implements ROADMAP.md §7 — Tailscale HTTPS
  hostname").
- This file is **append-only** for past items: don't rewrite shipped
  ROADMAP items, even if the shipped version differs from the estimate
  here. Add a one-line note instead.
