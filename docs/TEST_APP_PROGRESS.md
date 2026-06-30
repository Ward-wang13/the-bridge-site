# The Bridge Split Delivery Test Progress

Last updated: 2026-06-30

This document records the current exploration state for direction 1:

```text
Desktop scrape -> TAE cloud API/storage -> mobile/Android delivery
```

Keep this file as the handoff source for the test API project so future work
does not accidentally deploy experimental API behavior to the production
download/update app.

## Current Environment

- Desktop app repo: `/Users/ward/for_claude/the-bridge`
- Current desktop/Android mobile worktree:
  `/Users/ward/for_claude/the-bridge-mobile-ui`
- Cloud API/site repo: `/Users/ward/for_claude/the-bridge-site`
- Current permanent pairing API worktree:
  `/Users/ward/for_claude/the-bridge-site-permanent-pairing`
- Production TAE app: `thebridge`
- Production domain: `https://thebridge.tae.vera-mesh.com`
- Production image:
  `registry.pixcakeai.com/tae/the-bridge-site:data-static-202606111538`
- Isolated test API app: `thebridgesite`
- Test API domain: `https://thebridgesite.tae.vera-mesh.com`
- Test API image:
  `registry.pixcakeai.com/tae/the-bridge-site:202606222145-device-pairing`
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
- `POST /api/mobile-assets`
- `GET /api/mobile-assets/:id`
- `POST /api/device-pairings`
- `POST /api/device-pairings/exchange`
- `GET /api/devices`
- `POST /api/devices/:id/revoke`

User-data endpoints require a bearer token. The server derives `owner_key` from
the token and only reads/writes rows for the current user.

Send tasks are generated from an existing scrape batch that belongs to the
current user. The service expands the batch's `customers` array into
`send_task_items`, one pending item per customer. A mobile/Android sender can
list tasks, open a task, claim the next pending item, send it, and write back
item status with result data.

Mobile sender flow:

1. Pair once from Android using a desktop-generated pairing code.
2. Store the returned long-lived `tbdev_` device token locally.
3. Call `GET /api/send-tasks` to list the current user's tasks.
4. Call `GET /api/send-tasks/:id` to inspect a task if needed.
5. Call `POST /api/send-tasks/:id/claim-next` with optional
   `{ "worker_id": "android-device-id" }`.
6. If the response has `item: null`, the task has no pending work.
7. Send the returned item through the mobile sender.
8. Call `POST /api/send-task-items/:id/result` with `success`, `failed`,
   `skipped`, or `pending`.

Mobile device management:

- Pairing codes remain short-lived and single-use.
- The exchanged `tbdev_` token is the persistent phone binding.
- The desktop user token can list and revoke bound phones.
- Device tokens can consume task APIs but cannot create tasks, list devices, or
  revoke devices.
- Revoked device tokens receive `401`; Android clears the local binding and asks
  the user to pair again.
- Re-pairing the same device replaces the old active binding.

Legacy flow, preserved for historical context:

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

Last known result on 2026-06-30 from
`/Users/ward/for_claude/the-bridge-site-permanent-pairing`:
`22 tests OK`.

## Implemented Desktop Upload

In `/Users/ward/for_claude/the-bridge`:

- Added cloud client:
  - `src/cloud/client.py`
  - `src/cloud/__init__.py`
- Added config key:
  `cloud_api_base_url`
- Added desktop API methods for upload and cloud task creation/list/detail.
- Added desktop API methods for mobile pairing and device management:
  - `create_mobile_pairing_code()`
  - `list_mobile_devices()`
  - `revoke_mobile_device()`
- Added scraper page cloud task panel:
  - Button: `生成云端任务`
  - Panel title: `云端发送任务`
- Moved the phone pairing/device management UI into Settings:
  - Generate phone pairing code.
  - Refresh bound phones.
  - Revoke a bound phone with confirmation.
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

Last known desktop/Android worktree targeted result on 2026-06-30:

- `/Users/ward/for_claude/the-bridge/venv/bin/python -m pytest tests/test_cloud_client.py tests/test_api.py -q`
  from `/Users/ward/for_claude/the-bridge-mobile-ui`: `44 passed`.
- `node tests/js/test_scraper_mobile_devices.js`: passed.
- Android API client harness:
  `BridgeApiClientAssetDownloadTest OK`.
- Android debug build:
  `cd mobile/android-sender && ./gradlew :app:assembleDebug`: build successful.

Note: `./gradlew :app:testDebugUnitTest` is not the current Android test entry
point because the project uses zero-dependency `main()` Java harnesses rather
than JUnit tests.

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

Desktop repo historical baseline:

- Branch: `feature/explore`
- Pushed commit:
  `11f42d6 feat: add desktop cloud task panel`
- Pushed to:
  - `origin feature/explore`
  - `backup feature/explore`

Current desktop/Android mobile worktree:

- Worktree:
  `/Users/ward/for_claude/the-bridge-mobile-ui`
- Branch:
  `feature/android-sender-two-tab-ui`
- Current local HEAD before the 2026-06-30 doc/update commit:
  `6367d31 fix(desktop): place mobile pairing in settings`
- Remote baseline:
  `92e7945 feat(android): redesign sender app shell`
  on `origin/feature/android-sender-two-tab-ui` and
  `backup/feature/android-sender-two-tab-ui`.
- Unsubmitted local changes as of this handoff:
  - Android API diagnostic probes unauthenticated `GET /api/health` instead of
    authenticated `GET /api/send-tasks`.
  - `BridgeApiClientAssetDownloadTest` covers diagnostic path and header
    behavior.
  - `setup.py` and `scripts/pack_mac.sh` include `ui/js/auth.js` in packaged
    desktop builds.

Site/API repo historical baseline:

- Branch: `main`
- Pushed commits:
  - `535f096 feat: add authenticated cloud API for scrape batches`
  - `66f3e0d fix: use supported internal python base image`
  - `d56b05f feat: add send task queue API`
  - `9945a4e feat: add send task item claiming`
  - `c4771d7 chore: tighten docker build context`

Current permanent pairing API worktree:

- Worktree:
  `/Users/ward/for_claude/the-bridge-site-permanent-pairing`
- Branch:
  `feature/permanent-mobile-pairing`
- Current HEAD before the 2026-06-30 doc/update commit:
  `32371b6 fix(api): replace duplicate mobile device binding`
- Branch contains:
  - protected mobile image assets;
  - device pairing API;
  - paired user info in pairing exchange;
  - mobile device listing/revocation;
  - legacy device token public-id migration;
  - same-device re-pair replacement.

## Latest Deployment Smoke

Deployment on 2026-06-22:

- Test app: `thebridgesite`
- Image: `registry.pixcakeai.com/tae/the-bridge-site:202606222145-device-pairing`
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

Mobile pairing smoke:

- Desktop local config points at:
  `https://thebridgesite.tae.vera-mesh.com`
- Desktop auth token was valid.
- `POST /api/device-pairings -> 201`
- `POST /api/device-pairings/exchange -> 200`
- Returned token prefix: `tbdev_`
- Device token `GET /api/send-tasks -> 200`
- Visible task count through device token: `1`
- Visible task id:
  `acf9e40b2f7946709b548f16f3dbd18e`
- Android debug APK with pairing UI was installed on USB device
  `2312DRA50C`.

Persistent pairing revocation smoke on 2026-06-30:

- Desktop auth token was valid:
  `GET /api/me -> 200`
- Android debug APK from `feature/android-sender-two-tab-ui` was installed on
  USB device `2312DRA50C` with app data preserved.
- Existing Android device token could list tasks:
  `GET /api/send-tasks -> 200`, visible task count `31`.
- Active phone binding before revoke:
  `dev_RlpucOuiV84UFAfr`
  (`device_id = android-a66480ec7fa15931`).
- Desktop user token revoked that phone:
  `POST /api/devices/dev_RlpucOuiV84UFAfr/revoke -> 200`.
- Direct phone networking to `thebridgesite.tae.vera-mesh.com:443` timed out
  during the smoke because the phone resolved the host to `172.19.74.232`.
  To isolate Android revocation behavior, a temporary Mac-side proxy was exposed
  with `adb reverse tcp:18081 tcp:18081`, and Android `api_base_url` was
  temporarily set to `http://127.0.0.1:18081`.
- Through the proxy, Android refresh received server `401`, cleared the local
  `token`, cleared paired user fields, switched to `未配对`, and displayed:
  `手机绑定已失效，请在电脑端重新生成配对码`.
- A fresh pairing code was generated and entered on Android. The phone stored a
  new `tbdev_` token, displayed `已永久绑定：汪凯琦，可以刷新任务`, and local
  `api_base_url` was restored to `https://thebridgesite.tae.vera-mesh.com`.
- New active phone binding after recovery:
  `dev_AM--asxD1d7_0P_C`.
- New device token verification:
  `GET /api/send-tasks -> 200`, visible task count `31`.

Conclusion: server revocation, Android 401 handling, local binding cleanup, and
re-pair recovery were verified on a real device. Direct phone access to the test
API domain remains dependent on the phone network's DNS/routing.

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

1. Continue the Android sender MVP in the desktop repo:
   `/Users/ward/for_claude/the-bridge/mobile/android-sender`
   - Current first pass uses desktop-generated pairing codes.
   - Android exchanges the pairing code for a limited `tbdev_` device token.
   - It can list send tasks, select a task, claim next, and manually write
     `success`, `failed`, or `skipped`.
   - It does not yet do final message rendering or Enterprise WeChat
     AccessibilityService sending.

2. Add the message payload contract for mobile sending.
   - Ensure each claimed item includes the final message text, optional image
     reference, `send_order`, and searchable contact keys.
   - Avoid making Android reimplement desktop classification/template logic.

3. Build the Android AccessibilityService sender.
   - Search Enterprise WeChat contacts.
   - Send text/image in the requested order.
   - Record failed/skipped reasons.
   - Write the result back through `/api/send-task-items/:id/result`.

4. Add real-token isolation smoke testing if possible.
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
