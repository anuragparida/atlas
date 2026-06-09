# Atlas NTFY config

Self-hosted NTFY server for the Atlas stack. Single-user, single-purpose:
only the `atlas-notifier` publishes, only the iOS NTFY app subscribes, and
the only topic family is `atlas-<userid>`.

This directory contains three things:

| File | What it is | When you edit it |
|---|---|---|
| `server.yml` | The NTFY server config. Bind-mounted into the container. | When you change port, rate limits, or auth policy. |
| `issue_token.py` | The one-time bootstrap script. Idempotent. | When you rotate a token or add a second user. |
| `pyproject.toml` | The bootstrap script's project metadata. | Rarely. |

## First install (on openclaw)

```bash
# 1. Bring up the container.
cd /home/ody/workspace/atlas
docker compose up -d ntfy
docker compose ps ntfy   # wait for "healthy"

# 2. Run the bootstrap script. It will prompt for the iOS subscriber's
#    password (the password the iOS NTFY app will use to authenticate
#    when subscribing to atlas-<userid>).
uv run --with ./pyproject.toml docker/ntfy/issue_token.py --user-id anurag

# 3. Copy the printed tokens:
#    - Subscribe token  → paste into the iOS NTFY app subscription
#                          (server: http://openclaw:8090, topic: atlas-anurag,
#                           auth: paste the token as the password field)
#    - Publish token    → set as NTFY_PUBLISH_TOKEN in docker/notifier/.env
```

The bootstrap is idempotent: re-running with the same `--user-id` prints the
same tokens (no rotation). To rotate a token, run `ntfy token remove` inside
the container for the old label, then re-run the script.

## Verifying the ACL

The config in `server.yml` sets `auth-default-access: deny-all`, so a bare
request to any topic must be rejected. After install:

```bash
# Must return 401 (unauthenticated) or 403.
curl -i http://openclaw:8090/atlas-anurag

# Must return 200 and a JSON event stream.
curl -i -H "Authorization: Bearer $SUBSCRIBE_TOKEN" \
     http://openclaw:8090/atlas-anurag/json?poll=1

# Must return 200 and the message lands in the topic.
curl -i -H "Authorization: Bearer $PUBLISH_TOKEN" \
     -d "test from curl" \
     http://openclaw:8090/atlas-anurag
```

If the first command returns 200, the auth config is wrong; the default
must be `deny-all` and the per-topic ACLs must be in place.

## What the auth model guarantees

- **Anonymous reads are denied.** With `auth-default-access: deny-all`, no
  topic is readable without a token. The LAN user running `curl` cannot
  read the notifier's chat-completion messages.
- **Anonymous writes are denied.** Same reason. The notifier is the only
  writer, via its `tk_atlas_<userid>_publish` token.
- **The publish token cannot subscribe.** It belongs to a separate user
  (`<userid>-publisher`) whose only ACL is `write-only` on the topic. Even
  if the token leaks, the attacker cannot read other topics or the cached
  history.
- **The subscribe token cannot publish.** It belongs to `<userid>`, whose
  ACL is `read-only` on the topic. Even if the iOS device is compromised,
  the attacker cannot inject fake completion events.
- **Token rotation is independent.** The two tokens are owned by different
  users, so rotating the publish token (e.g. after the notifier host
  changes) does not require re-installing the iOS app, and vice versa.

## What the config does NOT do

- **No TLS.** NTFY is reached over the LAN (`http://openclaw:8090`) and
  Tailscale (`http://100.83.146.18:8090`). Both are already encrypted by
  their layers; adding TLS at the NTFY layer would be redundant.
- **No federation.** We are not part of the global NTFY network.
- **No email/webhook publishers.** Pure pub-sub for NTFY → iOS.
- **No external auth (OIDC, etc.).** The single local user is fine for v1.
