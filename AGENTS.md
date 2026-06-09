# atlas — AGENTS.md

> Project-level onboarding for AI agents (and humans) working in `atlas/`.
> The cross-project baseline lives at `../AGENTS.md`; this file overrides it
> for atlas-specific rules.

## What this is

Native iOS app: a WebView around Open WebUI with LAN/Tailscale reachability
and NTFY push. Two-app umbrella (Atlas Chat + Atlas Hermes). See `README.md`
for the project pitch and the docker-compose stack (`atlas-notifier` service,
NTFY topic config, per-topic ACL).

**Current phase:** Phase 2 (atlas-notifier Python service). Phase 1 was the
initial iOS app shell; Phase 2 added the supervisor + NTFY integration.

## Hard rules for this repo

### Commit cadence (overrides cross-project default)

- **One commit per kanban card, at minimum.** Card IDs (`t_xxxxxxxx`) in
  the subject. Format: `Atlas Phase N (card N): <what changed>` (Phase 1
  convention) or `fix(<area>): <what changed>` for non-card fixes.
- **Commit before yielding.** Same logic as clausecraft — the next agent or
  reviewer reads `git log`, not the working tree.
- **Phase-end = merge to `main` + push.** The `feature/atlas-notifier`
  branch is mid-Phase 2 and has 8 commits ahead of `main` (which is at
  "Initial commit"). That gap should close at the end of Phase 2.

### Secrets — never commit, never push

- `.webui_secret_key` (Flask session secret for Open WebUI). **Already
  created locally on 2026-06-08, do not commit.** Currently untracked, and
  there is no `.gitignore` entry for it yet — that entry must land in the
  same commit that closes out the Phase 2 migration.
- `.env`, `.env.local`. Already in `.gitignore`. Don't fight it.
- `ios/` and `android/` (native build outputs from `npx expo prebuild`) are
  in `.gitignore`. Don't commit them.

### Mobile / native targets — exempt from "run-it-for-Anurag" rule

Per the cross-project AGENTS.md, mobile targets don't need
`docker compose up`. They build via Xcode / `npx expo run:ios`. So for
atlas, the verification command is:

```bash
# from /home/ody/workspace/atlas
sg docker -c "docker compose ps atlas-notifier"   # service healthy
curl -s -w 'ntfy=%{http_code}\n' -o /dev/null http://localhost:12586/  # if exposed
```

The iOS app itself is verified by Anurag on-device, not by curl.

## Tech stack (project-specific)

- **iOS app:** React Native (Expo). The actual native iOS build is generated
  via `npx expo prebuild` and lives in `ios/` and `android/` (gitignored).
- **atlas-notifier service:** Python 3.12, supervisor pattern. Polls the
  Open WebUI `/api/chats` endpoint to detect chat-status transitions, fires
  NTFY push notifications with image attachments via `binwiederhier/ntfy`.
- **NTFY:** `binwiederhier/ntfy` (NOT the deprecated `ntfy/ntfy` image).
  Per-topic ACL, token bootstrap. See docker-compose.yml.
- **No CI / no deploy.** Same as clausecraft — portfolio project, push to
  GitHub is enough.

## Things that look like bugs but aren't

| Looks like… | Reality |
|---|---|
| `feature/atlas-notifier` is the only branch with code | Legacy of the previous workflow. Merge to `main` at end of Phase 2. |
| `.webui_secret_key` is sitting at the repo root untracked | It IS a real secret (Flask session key for Open WebUI). The migration commit must add it to `.gitignore` before the secret is ever at risk of being staged. |
| `ios/` and `android/` are missing | That's correct — they're build outputs and are in `.gitignore`. |

## When in doubt

- Read `README.md` for the project pitch.
- Read `../AGENTS.md` for cross-project rules.
