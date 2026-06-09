# Atlas

Native iOS app: a WebView around Open WebUI, with LAN/Tailscale reachability and NTFY push notifications. Ships as one iOS app (`Atlas Chat`) today; a second app (`Atlas Hermes`) is on the roadmap.

The full design lives in `SPEC.md`. The Phase 2 server-side stack (atlas-notifier Python service + NTFY Docker stack) is spec-only in `PHASE2-SPEC.md` — the code is in `docker/notifier/` and `docker/ntfy/`. This file is the cold-install runbook for the as-shipped stack.

This runbook is tested against a clean install on `openclaw` with the live NTFY 2.24.0 image and the current Open WebUI build. Where the spec and the live install diverge, the runbook uses what actually works.

---

## 0. What you actually run

Three things, in two places.

| Where | What | Runs on port |
|---|---|---|
| MacBook | Expo scaffold + EAS build | (builds the `.ipa`) |
| iPhone | Atlas Chat (the WebView) + the NTFY app (the push) | (talks out) |
| openclaw | Open WebUI (the chat) + atlas-notifier (the listener) + atlas-ntfy (the push server) | 8080 / — / 8090 |

If you only want the chat on the iPhone, you don't need atlas-notifier or NTFY. If you want completion notifications on the iPhone when Atlas is backgrounded, you need all three.

---

## 1. Prereqs (one-time, per machine)

**On the MacBook:**

- macOS, Node 18+, pnpm (`npm install -g pnpm`).
- EAS CLI: `npm install -g eas-cli`.
- AltServer: download from [altstore.io](https://altstore.io), drop in `/Applications`, launch it (lives in the menu bar).
- An Expo account: free, sign up at [expo.dev/signup](https://expo.dev/signup).
- An Apple ID, signed into iCloud on the iPhone. Free tier, no Apple Developer $99/yr.

**On the iPhone:**

- iOS, AltStore installed (App Store, free).
- Tailscale installed and logged in to your tailnet.
- The **ntfy** iOS app by Philipp Heckel (App Store, free, no IAP).

**On openclaw (the server):**

- Docker + Docker Compose.
- You must be in the `docker` group, or be ready to wrap docker commands in `sg docker -c "..."`. The bootstrap script in §2 shells out to `docker exec` and fails silently on permission errors if your user can't reach the docker socket.
- The `atlas-net` Docker bridge network (external, shared with the Open WebUI stack):

  ```bash
  docker network create atlas-net
  ```

  If your Open WebUI stack already defines its own network, add `atlas-net` as an external network to that compose file too. Atlas names it `atlas-net` because the `docker-compose.yml` in this repo references it by that name.
- An Open WebUI install that exposes port 8080 on openclaw's LAN IP (`http://192.168.178.123:8080`) and Tailscale IP (`http://100.83.146.18:8080`), reachable to the iPhone. The Open WebUI install is out of scope for this repo — see `clausecraft/` for the canonical stack.
- An Open WebUI **service-account API key**: Open WebUI admin UI → Settings → Account → API Keys → generate. The notifier uses this; you'll paste it into `docker/notifier/.env` in §2.

**Verify the openclaw side from the MacBook terminal before doing anything else:**

```bash
curl -fsS http://192.168.178.123:8080/health    # expect 200 (Open WebUI is up)
curl -fsS http://100.83.146.18:8080/health      # expect 200
```

If either fails, fix openclaw first. The rest of this runbook assumes Open WebUI is healthy on both addresses.

---

## 2. Server-side install (openclaw)

Run from the MacBook, `ssh`-ed into openclaw (or a local terminal there). `cd` into the repo, then:

```bash
# 1. Create the .env file the notifier reads. The real tokens are
#    produced in step 4; you paste them in at the end.
cp docker/notifier/.env.example docker/notifier/.env
chmod 600 docker/notifier/.env
$EDITOR docker/notifier/.env
#   fill in:
#     OPENWEBUI_BASE_URL=http://192.168.178.123:8080
#     OPENWEBUI_API_KEY=*** service-account key from §1...
#     ATLAS_USER_ID=anurag        # see step 3 for why this is hardcoded
#   leave NTFY_PUBLISH_TOKEN=*** for now; the bootstrap prints it

# 2. Bring up the stack. atlas-notifier is the listener; atlas-ntfy is the
#    push server.
docker compose -f docker/docker-compose.yml up -d
docker compose -f docker/docker-compose.yml ps   # wait for both "healthy"
```

Healthcheck is 30s, with a 20s start_period for the notifier, so `up` to "healthy" is typically 30–60s. If either service is stuck in `starting` past 90s, see §8.

### 3. Pick a topic name (the `ATLAS_USER_ID`)

The NTFY topic name is `atlas-<userid>`. Per `PHASE2-SPEC.md` §1.4, the spec says the notifier should look up the Open WebUI user's stable id (`GET /api/v1/auths/me/`) and use that UUID as `<userid>`. In practice on the current Open WebUI build, that endpoint returns the SPA HTML rather than JSON, so the discovery path doesn't work — and a memorable short name like `anurag` is friendlier to type into the iOS NTFY app than a UUID anyway.

Set `ATLAS_USER_ID=anurag` in `docker/notifier/.env` (lowercase, alphanumeric + `-`/`_` only). The notifier uses this to construct the topic name, the iOS NTFY app subscribes to the same name, and the two stay in sync without a UUID lookup.

### 4. Bootstrap the NTFY tokens

The bootstrap creates the iOS subscriber user, the notifier publisher user, the per-topic ACL, and prints two tokens. It's idempotent — re-running with the same `--user-id` re-prints the same tokens.

**Note on the bootstrap script.** `docker/ntfy/issue_token.py` is the documented bootstrap path. On the current NTFY image (`binwiederhier/ntfy:v2`, version 2.24.0) the script's `probe_schema` step fails because the image does not ship the `sqlite3` CLI, so the script can't introspect the auth database. The user-creation and ACL parts still work; the idempotency check doesn't. Run it once. If it fails on the schema probe, the manual path below is the workaround.

```bash
cd /home/ody/workspace/atlas

# Run the bootstrap. It will prompt for a password (this is what the iOS
# NTFY app uses to authenticate at subscription time; you only type it
# once). On a re-run with the same --user-id, the password prompt is
# skipped and the existing tokens are reprinted.
sg docker -c "uv run --project docker/ntfy docker/ntfy/issue_token.py --user-id anurag"
#   → prints two tokens:
#     Subscribe token (paste into the iOS NTFY app)
#     Publish token   (set as NTFY_PUBLISH_TOKEN in docker/notifier/.env)
```

If the script errors on `probe_schema` ("auth.db is missing tables"), the database schema is fine — the issue is the missing `sqlite3` CLI in the NTFY image. The fix is the manual path.

Pick a password for the iOS subscriber user first. The publisher user doesn't need a password you'll ever type — it authenticates by token only, so a long random string is fine.

```bash
# Set the iOS subscriber password. The publisher user gets a random
# string that no human will ever type.
export IOS_PASSWORD='set me to a memorable password'  # paste into the iOS NTFY app
export PUB_PASSWORD=$(openssl rand -base64 24)        # never typed by a human

# Create the two NTFY users. NTFY_PASSWORD env var keeps the call
# non-interactive (the prompt is meant for `docker exec -it` sessions).
# Use single quotes around the sg docker -c "..." payload so the
# shell expands $IOS_PASSWORD / $PUB_PASSWORD on the openclaw side,
# not inside the docker container.
sg docker -c 'docker exec -e NTFY_PASSWORD='"$IOS_PASSWORD"' atlas-ntfy ntfy user add --role=user anurag'
sg docker -c 'docker exec -e NTFY_PASSWORD='"$PUB_PASSWORD"' atlas-ntfy ntfy user add --role=user anurag-publisher'
# "user already exists" means the user is already there — fine, skip.

# ACL: subscriber gets read-only, publisher gets write-only. Setting a
# permission overwrites any prior rule for the same <user, topic>.
sg docker -c "docker exec atlas-ntfy ntfy access anurag atlas-anurag read-only"
sg docker -c "docker exec atlas-ntfy ntfy access anurag-publisher atlas-anurag write-only"

# Issue the two tokens. The token is printed to stdout — save it.
sg docker -c "docker exec atlas-ntfy ntfy token add --label=atlas-subscribe anurag"
# → prints the subscribe token. Paste this into the iOS NTFY app.
sg docker -c "docker exec atlas-ntfy ntfy token add --label=atlas-publish anurag-publisher"
# → prints the publish token. Paste this into docker/notifier/.env.
```

Wire the publish token into the notifier's env, then restart the notifier so it picks it up:

```bash
# Edit docker/notifier/.env and set:
#   NTFY_PUBLISH_TOKEN=*** token you just got'
docker compose -f docker/docker-compose.yml restart atlas-notifier
docker compose -f docker/docker-compose.yml ps atlas-notifier   # healthy
```

### 5. End-to-end smoke test from openclaw

Proves Open WebUI → notifier → NTFY round-trips. The notifier speaks to NTFY on the internal `atlas-net` bridge; the host port (`8090`) is bound to the docker-proxy and the iPhone reaches NTFY through that proxy. So the smoke test uses the **internal** NTFY URL, not the host URL.

```bash
# From openclaw: the notifier should be publishing. Send a chat in Open
# WebUI and check the notifier's log for a 'ntfy_published' line.
docker logs atlas-notifier --tail 20 | grep -E 'publishing_completion|ntfy_published'
# → should see one line per chat completion.

# Send a fake completion directly to NTFY. This is the only smoke test
# that works from the host: authenticate to NTFY via the docker network
# by running curl from inside the atlas-notifier container, which can
# reach atlas-ntfy:8090 directly (not via the docker-proxy).
sg docker -c "docker exec atlas-notifier python -c '
import os, urllib.request
req = urllib.request.Request(
    \"http://atlas-ntfy:8090/atlas-anurag\",
    data=b\"smoke test from openclaw\",
    headers={\"Authorization\": \"Bearer \" + os.environ[\"NTFY_PUBLISH_TOKEN\"]},
    method=\"POST\",
)
try:
    r = urllib.request.urlopen(req, timeout=3)
    print(\"publish:\", r.status, r.read()[:120])
except Exception as e:
    print(\"publish err:\", type(e).__name__, e)
'"
# → expect "publish: 200 {..." if the token is right.
```

If the publish returns 200, the server-side stack is working. The iPhone's NTFY app is what delivers the actual notification — confirm that in §4.

The host-port curl in the spec's smoke-test section (`curl http://192.168.178.123:8090/...`) returns 401 even when the install is working. Don't use that as a smoke test; it's a docker-proxy artifact. Use the in-container curl above.

---

## 3. iOS app install (MacBook)

The first install of each EAS build costs a free-tier build quota slot and 10–20 minutes. The `eas.json` and `app.config.ts` are already committed. **Do not run `eas build:configure`** — it would clobber the committed `eas.json`. If `eas build` ever complains about a missing file, the most likely cause is a prior `eas build:configure` that overwrote the file; check `git diff eas.json app.config.ts` and revert.

```bash
cd atlas
pnpm install
pnpm typecheck    # tsc --noEmit, expect 0 errors

# One-time: log into Expo.
eas login

# Build the .ipa. This is the slow step (~15 min).
eas build --platform ios --profile development
```

EAS prints a URL when the build is done. Open it, download the `.ipa`.

**Install on the iPhone:**

1. Plug the iPhone into the MacBook (or AirDrop the `.ipa` to the iPhone).
2. AltStore on the iPhone: tap **My Apps → +** in the top-left, pick the `.ipa`.
3. Trust the developer profile: iOS **Settings → General → VPN & Device Management → [your Apple ID] → Trust**.

Cold-start the app. It should open Open WebUI within ~1.5s on Wi-Fi.

---

## 4. NTFY app install (iPhone)

The NTFY iOS app is the channel atlas-notifier pushes to. You configured the server side in §2; this section wires the iPhone to it.

1. App Store → search **"ntfy"** → install the official NTFY app by Philipp Heckel (free, no IAP).
2. Open the app. Tap **+** to add a subscription.
3. **Server URL:** `http://100.83.146.18:8090` (Tailscale, since NTFY is openclaw-side, not on the public internet).
   - LAN alternative: `http://192.168.178.123:8090` if the iPhone is on home Wi-Fi.
   - If you ever add TLS or a public hostname, change this and re-test.
4. **Topic:** `atlas-anurag` (or whatever you set `ATLAS_USER_ID` to in §3). Lowercase, no spaces, no leading slash.
5. **Auth:** tap into the auth field, select "Username + password," enter `anurag` as the username and the password you set during the bootstrap as the password. (Or use "Bearer token" with the **subscribe token** you saved from §2.4.)
6. Tap **Subscribe**. NTFY sends a "Test message" immediately. You should see it in the iPhone notification tray.

**Verify the round-trip end-to-end** — send a chat in Atlas Chat, lock the iPhone, wait ~3s. The notification should arrive. If the iPhone is foregrounded on Atlas Chat at the moment the reply lands, you won't see a banner (Open WebUI is already showing the reply) — this is intentional, see §6.

---

## 5. Reachability — what the pill actually means

The pill in the chat screen is the reachability state machine. Three states:

| Pill | State | What's happening |
|---|---|---|
| (no pill) | `lan` | LAN probe to `http://192.168.178.123:8080/health` is returning 200. Native speed. |
| `via VPN · shield` | `tailscale` | LAN probe failed; Tailscale probe to `http://100.83.146.18:8080/health` is returning 200. Network traffic is going over the tailnet. |
| "Can't reach openclaw" full screen | `unreachable` | Both probes failed. The app can't talk to Open WebUI at all. |

**Tailscale is the fallback, not the faster probe.** If you see "via VPN" while sitting on home Wi-Fi, the LAN probe tripped its 1s timeout — the iPhone may be on a guest VLAN, the AP may be isolating clients, or openclaw may be down on the LAN. The pill is informational. Toggling Tailscale off reverts within ~30s (one stable-state probe tick).

**Switching between LAN and Tailscale mid-conversation** is fine; the WebView reloads if the URL changes, but the unsent-input buffer in Zustand survives. If the chat cuts off mid-message, the unsent text is preserved and the message will resend on reload.

---

## 6. First-launch checklist

In order. Stop at the first failure.

1. **App opens to Open WebUI on home Wi-Fi.** If you see "Can't reach openclaw", check `curl http://192.168.178.123:8080/health` from the MacBook. If the MacBook can reach it but the iPhone can't, they're on different VLANs — fix the network before going further.
2. **Sign in to Open WebUI** (regular Open WebUI auth). Send a one-word message. Get a one-word reply. Confirms the WebView is wired and not just rendering cached HTML.
3. **Walk out of Wi-Fi range** (or toggle Wi-Fi off on the iPhone). Turn on Tailscale. The pill at the top should flip to "via VPN · shield icon". The chat should keep working without a reload flicker (or with one, then continue). If the app jumps straight to "Can't reach openclaw" while Tailscale is on, verify `curl http://100.83.146.18:8080/health` from the iPhone's Tailscale network.
4. **Background the app, send a chat, wait ~3s, see a notification.** This is the Phase 2 path. The atlas-notifier detects the chat flipping to idle and publishes to NTFY; the iOS NTFY app delivers the system banner. If the banner doesn't arrive, the publish path is broken — see §8.
5. **Lock the iPhone for a minute, unlock, see a fresh probe.** The probe runs in 5s cadence while unreachable and 30s when stable; it pauses in background. If the pill state ever looks wrong, force-quit and cold-start to reseed from AsyncStorage.
6. **Type into the chat, lock the iPhone immediately, unlock.** Whatever you typed should still be in the input box. The Zustand `unsentDraft` survives WebView reloads and is re-injected by `ChatWebView`.
7. **No probe in background.** Send a message, lock the iPhone, leave it for an hour. Battery delta in iOS Settings should be flat (or within noise). If it's draining, the probe is leaking.

The first three are must-pass for v1. Items 4–7 confirm Phase 2 + v1-quality; they're not strictly required for day-1 ship.

---

## 7. 7-day signature expiry

Free Apple IDs sign apps for 7 days. After that, Atlas refuses to launch: black screen, "this app is no longer available," or instant crash on open.

**Auto-refresh (the normal case):** AltServer on the MacBook + iPhone on the same Wi-Fi network re-signs the app silently in the background. Don't lock the MacBook with the lid closed overnight — it needs to be awake on the same network as the iPhone.

**Manual refresh (Mac went down, you're on the road):**

```bash
# on the MacBook, after plugging the iPhone in via USB
open -a AltStore
# My Apps → atlas → Refresh
```

30 seconds.

**If the app refuses to launch after 7+ days and you can't get to the MacBook**, re-install the `.ipa` from the EAS build URL the same way you did the first time. The new install is fresh-signed for another 7 days.

The 7-day clock is the price of skipping the $99. The trade is fine for daily-driver personal use; it's not fine for an app you hand to other people.

---

## 8. Troubleshooting

Work top-down. Each row assumes you've already failed the row above.

| Symptom | First check | Then |
|---|---|---|
| `docker compose up` fails on the `atlas-net` network | `docker network ls \| grep atlas-net` | The network is `external: true`; you must `docker network create atlas-net` first (§1). |
| `ntfy` is stuck in `starting` past 90s | `docker logs atlas-ntfy --tail 30` | Likely a port conflict on 8090, or a stale `ntfy-data` volume from a prior install. `docker compose down -v` wipes it (catastrophic — re-run §2). |
| `atlas-notifier` is stuck in `starting` past 90s | `docker logs atlas-notifier --tail 50` | Most often: `OPENWEBUI_API_KEY` wrong (401s from Open WebUI), or `OPENWEBUI_BASE_URL` unreachable. Fix `.env`, `docker compose restart atlas-notifier`. |
| `issue_token.py` errors with "auth.db is missing tables" | The NTFY image has no `sqlite3` CLI; the script's schema probe can't introspect the DB | Use the manual bootstrap commands in §4. |
| Anonymous read of `http://192.168.178.123:8090/atlas-anurag` returns 200 | That's a docker-proxy artifact, not a real auth failure | The notifier publishes to NTFY via the internal `atlas-net` bridge (which goes through NTFY's auth). Use the in-container smoke test in §5, not the host-port curl. |
| Anonymous read via the in-container curl returns 200 | `auth-default-access` is not `deny-all` | Re-check `docker/ntfy/server.yml`; the line must be `auth-default-access: "deny-all"`. The ACL on `<userid>` for `atlas-<userid>` is what allows the iOS app to subscribe. |
| `issue_token.py` errors with "permission denied" on docker socket | Your user isn't in the `docker` group | `sg docker -c "uv run --project docker/ntfy docker/ntfy/issue_token.py --user-id anurag"`, or add yourself to the docker group. |
| iPhone doesn't receive a test notification | The iOS NTFY app subscription is wrong, or the notifier isn't publishing | First: from the MacBook, send a chat in Open WebUI and check `docker logs atlas-notifier --tail 20` for a `publishing_completion` line. Then: re-check the iOS NTFY app's server URL, topic, and auth. The topic must match `atlas-<ATLAS_USER_ID>` exactly. |
| `eas build` fails on login | `eas whoami` | `eas logout && eas login`; verify the Expo account is the free one, not a paused org. |
| Build fails: "No bundle identifier" | `app.config.ts` has `ios.bundleIdentifier` | You ran `eas build:configure`; revert `app.config.ts` and `eas.json` from git. |
| Build fails: "No provisioning profile" (free Apple ID) | Normal for `production` profile | Use `--profile development` (default above). `production` requires a paid Apple Developer account. |
| `pnpm typecheck` reports errors in `node_modules` | Stale install | `rm -rf node_modules pnpm-lock.yaml && pnpm install`. |
| App installs, opens, black screen, then closes | 7-day signature expired | Re-install from the latest `.ipa`; see §7. |
| App installs, opens, "Can't reach openclaw" | `curl http://192.168.178.123:8080/health` from the MacBook | If that fails: openclaw is down, restart the stack. If it passes: iPhone isn't on the same LAN; join home Wi-Fi or turn on Tailscale. |
| App shows "via VPN" but on home Wi-Fi | Tailscale is on and the LAN probe tripped its 1s timeout (guest VLAN, AP isolation, or openclaw is down on the LAN) | Not a bug. The pill is informational. Toggling Tailscale off reverts within ~30s. |
| LAN fine, Tailscale fine, but app still says "Can't reach" | Wrong URL baked into `src/reachability/probe.ts` | Confirm `LAN_URL` and `TAILSCALE_URL` match your openclaw's actual addresses. Rebuild. |
| State pill flips during a single conversation | Probe reload is racing the WebView | Cosmetic. The `unsentDraft` is preserved. If it's not, file a card — that's a real bug. |
| App drains battery | Probe running while backgrounded | `useReachability` registers an `AppState` listener and `cancelTimer()`s on `background`. If you added a second probe, that's the leak. |
| Chat cuts off mid-message when walking between rooms | Probe reloaded the WebView before the new network path was confirmed stable | Cosmetic in v1. The unsent-input preservation in Zustand is wired. |
| `eas build` works but iPhone says "unable to install" | iPhone trust prompt never tapped | iOS Settings → General → VPN & Device Management → trust the Apple ID used in `eas login`. |
| AltStore says "Could not connect to AltServer" | MacBook is asleep or on a different network | Wake the MacBook, confirm same Wi-Fi as the iPhone. AltServer must be running. |
| Notification fires but message is empty or wrong | atlas-notifier hit Open WebUI before chat history was flushed | Cosmetic. Lock-screen banner is best-effort. Open Atlas Chat to see the actual reply. |
| `docker stats atlas-notifier` reports >80M RSS | Resource limit hit | Restart the notifier (`docker compose restart atlas-notifier`); the WS reconnect path spikes transiently. If it stays high, file a card with `docker stats --no-stream` output. |

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
| Change the NTFY topic (rename the user) | `docker/notifier/.env` + manual bootstrap in §2.4 | Set `ATLAS_USER_ID` to the new name, re-run the manual bootstrap with the new userid, update the iOS NTFY app's topic. |
| Rotate the NTFY publish token (after the notifier host changes) | `docker exec atlas-ntfy ntfy ...` | List the existing token (`ntfy token list anurag-publisher`), copy its value, then `ntfy token remove anurag-publisher tk_xxx`. Re-run the bootstrap's `ntfy token add --label=atlas-publish anurag-publisher` to get a new value, paste it into `docker/notifier/.env`, `docker compose restart atlas-notifier`. |
| Rotate the NTFY subscribe token (after the iPhone is reset) | same | Same as above with `anurag` and `atlas-subscribe`. The iOS NTFY app will need the new token re-pasted into its subscription's auth field. |
| Add a new build profile (e.g. `production` for TestFlight) | `eas.json` | Requires a paid Apple Developer account. Not in scope for v1 — see ROADMAP §8. |
| Add a new screen | `app/<screen>.tsx` | Expo Router auto-routes. `app/_layout.tsx` controls headers. |

When in doubt, the entry point is `app/index.tsx` — it owns the reachability hook and the WebView mount.

---

## 10. What's not in v1

The full deferred list is in `ROADMAP.md` — each item has a one-line description, an estimated scope (weekend / phase / full-project), and the open decision points. The headline items:

- **Atlas Hermes app** (`phase`): second iOS app in the same Expo codebase, different bundle id. ROADMAP §1.
- **Android client** (`weekend`, deferred indefinitely): scaffold carries the config, but the install + push story is its own work. ROADMAP §2.
- **Multi-user NTFY** (`phase`): per-user topics + per-user tokens. v1 is single-tenant. ROADMAP §3.
- **Tailscale HTTPS hostname** (`full-project`): serve Atlas on `https://openclaw.tail141210.ts.net` so the iPhone (and any browser) hits it on a stable URL. The blocker is port 443 colliding with the Tailscale daemon on openclaw. ROADMAP §7.
- **EAS production / TestFlight** (`weekend`, after paying the $99): adds a `production` profile. ROADMAP §8.
- **EAS Update (OTA JS)** (`weekend`): wire up JS-only updates so URL / probe-cadence tweaks don't need a full rebuild. The scaffold is ready for it (`cli.appVersionSource: "remote"` in `eas.json`); just needs an `update` block. ROADMAP §9.

---

## One-line summary

openclaw (`docker compose up` + `uv run --project docker/ntfy docker/ntfy/issue_token.py --user-id anurag`) → MacBook (`pnpm install && eas build --platform ios --profile development`) → iPhone (AltStore install + NTFY app subscribe to `atlas-anurag`) → ship.
