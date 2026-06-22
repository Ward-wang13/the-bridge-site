# The Bridge Split Delivery Test Progress

Last updated: 2026-06-22

This document records the current exploration state for direction 1:

```text
Desktop scrape -> TAE cloud API/storage -> mobile/Android delivery
```

Keep this file as the handoff source for the test API project so future work
does not accidentally deploy experimental API behavior to the production
download/update app.

## Current Environment

- Desktop app repo: `/Users/ward/for_claude/the-bridge`
- Cloud API/site repo: `/Users/ward/for_claude/the-bridge-site`
- Production TAE app: `thebridge`
- Production domain: `https://thebridge.tae.vera-mesh.com`
- Production image:
  `registry.pixcakeai.com/tae/the-bridge-site:data-static-202606111538`
- Isolated test API app: `thebridgesite`
- Test API domain: `https://thebridgesite.tae.vera-mesh.com`
- Test API image:
  `registry.pixcakeai.com/tae/the-bridge-site:20260622195604`
- Test API storage: `10Gi` mounted at `/data`
- Test API app data root: `/data/thebridgesite`
- Test API database path: `/data/thebridgesite/cloud/thebridge.db`

## Production Boundary

Do not deploy the Python API image to production app `thebridge` until static
download and update serving is redesigned.

Reason: the current Python stdlib server is suitable for the cloud API test app,
but it does not provide Nginx-equivalent static file behavior for desktop
downloads and OTA resources. In prior testing, the API image served normal GET
requests, but `HEAD` and byte range behavior were not good enough for production
DMG/update delivery.

Production `thebridge` must remain on the static Nginx image unless one of these
is implemented and verified:

- Nginx serves `/resources/*` and `/updates/*`, while reverse proxying `/api/*`
  to the API service.
- The API server is replaced with an implementation that fully supports
  production static file requirements including `HEAD` and byte ranges.

Large files must remain in TAE `/data`; do not bake DMG files or update zips into
the long-running web image.

## Auth Status

Unified login is implemented through `auth-gateway`.

- Callback URL whitelisted by admin:
  `http://127.0.0.1:53682/callback`
- Desktop auth files:
  - `/Users/ward/for_claude/the-bridge/src/auth/gateway.py`
  - `/Users/ward/for_claude/the-bridge/ui/js/auth.js`
- Desktop cloud upload uses `AuthManager.bearer_token()`.
- Cloud APIs require:
  `Authorization: Bearer <auth-gateway-jwt>`
- The cloud API calls auth-gateway `/userinfo` and derives ownership on the
  server.
- Do not trust client-provided user or owner fields.
- Current isolation key:
  `owner_key = org_id + ":" + union_id`

Conclusion: use auth-gateway as the identity source. Do not build a separate
login system for this exploration.

## Implemented Cloud API

In `/Users/ward/for_claude/the-bridge-site/server.py`:

- `GET /api/health`
- `GET /api/me`
- `POST /api/scrape-batches`
- `GET /api/scrape-batches`
- `GET /api/scrape-batches/:id`
- `POST /api/send-tasks`
- `GET /api/send-tasks`
- `GET /api/send-tasks/:id`
- `POST /api/send-tasks/:id/claim-next`
- `POST /api/send-task-items/:id/result`

User-data endpoints require a bearer token. The server derives `owner_key` from
the token and only reads/writes rows for the current user.

Send tasks are generated from an existing scrape batch that belongs to the
current user. The service expands the batch's `customers` array into
`send_task_items`, one pending item per customer. A mobile/Android sender can
list tasks, open a task, claim the next pending item, send it, and write back
item status with result data.

Mobile sender flow:

1. Login with auth-gateway and keep the bearer token.
2. Call `GET /api/send-tasks` to list the current user's tasks.
3. Call `GET /api/send-tasks/:id` to inspect a task if needed.
4. Call `POST /api/send-tasks/:id/claim-next` with optional
   `{ "worker_id": "android-device-id" }`.
5. If the response has `item: null`, the task has no pending work.
6. Send the returned item through the mobile sender.
7. Call `POST /api/send-task-items/:id/result` with `success`, `failed`,
   `skipped`, or `pending`.

Site tests:

```bash
cd /Users/ward/for_claude/the-bridge-site
python3 -W error::ResourceWarning -m unittest -v tests.test_server
```

Last known result: `14 tests OK`.

## Implemented Desktop Upload

In `/Users/ward/for_claude/the-bridge`:

- Added cloud client:
  - `src/cloud/client.py`
  - `src/cloud/__init__.py`
- Added config key:
  `cloud_api_base_url`
- Added desktop API methods for upload and cloud task creation/list/detail.
- Added scraper page cloud task panel:
  - Button: `生成云端任务`
  - Panel title: `云端发送任务`
- Upload removes local-only UI/cooldown fields.
- Upload keeps classification fields:
  - `category_id`
  - `template_id`
  - `matched_rule_id`

Desktop tests:

```bash
cd /Users/ward/for_claude/the-bridge
./venv/bin/python -m pytest -q
```

Last known result: `249 passed`.

## Local Desktop Test Configuration

The local desktop source run should point to the isolated test API app:

```text
cloud_api_base_url = https://thebridgesite.tae.vera-mesh.com
```

Current local config path:

```text
~/Library/Application Support/TheBridge/config.json
```

Set it with:

```bash
cd /Users/ward/for_claude/the-bridge
./venv/bin/python - <<'PY'
from src.config.manager import ConfigManager
mgr = ConfigManager()
mgr.save_config({
    "cloud_api_base_url": "https://thebridgesite.tae.vera-mesh.com",
})
print(mgr.load_config().get("cloud_api_base_url"))
PY
```

If uploads fail with `HTTP 405`, check whether the desktop app is accidentally
pointing at production `https://thebridge.tae.vera-mesh.com` instead of the test
API domain.

User-confirmed state: upload to the test API app has succeeded.

Real desktop upload smoke:

- Uploaded customer count: `281`
- Generated send task:
  `acf9e40b2f7946709b548f16f3dbd18e`
- Task item count: `281`
- Pending count after creation: `281`

## Git State To Preserve

Desktop repo:

- Branch: `feature/explore`
- Pushed commit:
  `11f42d6 feat: add desktop cloud task panel`
- Pushed to:
  - `origin feature/explore`
  - `backup feature/explore`

Site/API repo:

- Branch: `main`
- Pushed commits:
  - `535f096 feat: add authenticated cloud API for scrape batches`
  - `66f3e0d fix: use supported internal python base image`
  - `d56b05f feat: add send task queue API`
  - `9945a4e feat: add send task item claiming`
  - `c4771d7 chore: tighten docker build context`

## Latest Deployment Smoke

Deployment on 2026-06-22:

- Test app: `thebridgesite`
- Image: `registry.pixcakeai.com/tae/the-bridge-site:20260622195604`
- Health: `GET /api/health -> 200`
- Production app `thebridge` stayed on:
  `registry.pixcakeai.com/tae/the-bridge-site:data-static-202606111538`

Real-token task API smoke:

- `GET /api/send-tasks -> 200`
- Visible task count: `1`
- Smoke task id: `acf9e40b2f7946709b548f16f3dbd18e`
- Before claim: `281` pending items
- `POST /api/send-tasks/:id/claim-next -> 200`
- Claimed item status: `in_progress`
- Claimed item worker id: `codex-smoke`
- After claim: `280` pending items
- The smoke item was reset to `pending`.
- Final pending count: `281`

Design doc:

- `/Users/ward/for_claude/the-bridge/docs/SPLIT_DELIVERY_AUTH_DATA_PLAN.md`
- Pushed commit:
  `2fc9ec4 docs: define split delivery auth data isolation plan`

## Skills And Deployment Rules

For TAE image/build/deploy/app work, use the TS Skill Platform managed path.

Manager status from this exploration:

- `ts-skill` manager version: `0.8.19`
- Manager digest:
  `sha256:edd9810447a6e77e4d58077951a64787b87642d5a08a405adbfd640f67be2561`
- `tae-app-manager` version: `0.3.2`
- `tae-app-manager` digest:
  `sha256:75b9ca01c261760882b6a7d4769339e6ab447a91235a74afb43359864da4e558`
- Last managed run hash:
  `srh_79c98b1c3eff91b65f75bbdbad02f7a416c30ee83713bc4a`

Use:

```bash
~/.codex/skills/ts-skill-platform/scripts/ts-skill use tae-app-manager
```

or, if available in `PATH`:

```bash
ts-skill use tae-app-manager
```

## Next Recommended Work

1. Finish and deploy mobile/Android sender consumption API.
   - Commit `POST /api/send-tasks/:id/claim-next`
   - Deploy only to test app `thebridgesite`
   - Smoke test `claim-next` with the real desktop-created task

2. Add mobile/Android sender client.
   - Authenticate with auth-gateway
   - Pull `GET /api/send-tasks`
   - Open `GET /api/send-tasks/:id`
   - Claim work with `POST /api/send-tasks/:id/claim-next`
   - Send the claimed item
   - Write result through `POST /api/send-task-items/:id/result`

3. Add real-token isolation smoke testing if possible.
   - Verify `/api/me`
   - Verify task list/detail with the same login
   - Verify a second user cannot see or claim the first user's tasks

## Hard Constraints

- Do not run desktop release scripts during exploration.
- Do not run `scripts/release.sh`.
- Do not run `scripts/publish_update.sh`.
- Do not change desktop `src/version.py` `CODE_VERSION`.
- Do not deploy the API image to production `thebridge`.
- Do not store user ownership based on client-submitted fields.
- Do not mix production static download/update serving with the test API app.
