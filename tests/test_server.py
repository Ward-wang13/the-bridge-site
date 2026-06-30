from __future__ import annotations

import json
import hashlib
import base64
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

import server as bridge_server


class QuietBridgeHandler(bridge_server.BridgeHandler):
    def log_message(self, fmt, *args):
        pass


class StubAuthClient:
    def __init__(self, users=None, user=None, error=None):
        default_user = {
            "email": "u@example.com",
            "name": "User",
            "provider": "corp",
            "org_id": "org-1",
            "union_id": "union-1",
            "departments": ["Sales"],
        }
        self.user = user or default_user
        self.users = users or {}
        self.error = error
        self.tokens = []

    def userinfo(self, token):
        self.tokens.append(token)
        if self.error:
            raise self.error
        return self.users.get(token, self.user)


class ServerTestCase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.storage = bridge_server.Storage(Path(self.tmpdir.name) / "thebridge.db")

    def tearDown(self):
        self.tmpdir.cleanup()

    def run_server(self, auth_client):
        httpd = bridge_server.BridgeHTTPServer(("", 0), QuietBridgeHandler)
        httpd.auth_client = auth_client
        httpd.storage = self.storage
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        _, port = httpd.server_address
        return httpd, f"http://127.0.0.1:{port}"

    def stop_server(self, httpd):
        httpd.shutdown()
        httpd.server_close()

    def get_json(self, url, token=None):
        headers = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))

    def get_bytes(self, url, token=None):
        headers = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, resp.headers, resp.read()

    def post_json(self, url, payload, token=None):
        body = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))

    def test_extract_bearer_token(self):
        self.assertEqual(bridge_server.extract_bearer_token("Bearer abc"), "abc")
        self.assertEqual(bridge_server.extract_bearer_token("bearer abc"), "abc")
        self.assertEqual(bridge_server.extract_bearer_token("Basic abc"), "")
        self.assertEqual(bridge_server.extract_bearer_token(""), "")

    def test_derive_owner_key_prefers_org_and_union_id(self):
        owner = bridge_server.derive_owner_key({"org_id": "org", "union_id": "u", "email": "e"})
        self.assertEqual(owner, "org:u")

    def test_derive_owner_key_falls_back_to_email(self):
        owner = bridge_server.derive_owner_key({"org_id": "org", "email": "u@example.com"})
        self.assertEqual(owner, "org:email:u@example.com")

    def test_storage_migrates_legacy_device_tokens_without_public_id(self):
        db_path = Path(self.tmpdir.name) / "legacy.db"
        with bridge_server.sqlite3.connect(db_path) as conn:
            conn.executescript(
                """
                create table device_tokens(
                  token_hash text primary key,
                  owner_key text not null,
                  device_name text not null,
                  device_id text not null,
                  created_at text not null,
                  last_seen_at text not null,
                  revoked_at text
                );
                insert into device_tokens(
                  token_hash, owner_key, device_name, device_id,
                  created_at, last_seen_at, revoked_at
                ) values (
                  'hash-1', 'org:user', 'Android', 'android-1',
                  '2026-06-01T00:00:00+00:00', '2026-06-01T00:00:00+00:00', null
                );
                """
            )

        storage = bridge_server.Storage(db_path)
        devices = storage.list_device_tokens("org:user")

        self.assertEqual(len(devices), 1)
        self.assertTrue(devices[0]["id"].startswith("dev_"))
        with bridge_server.sqlite3.connect(db_path) as conn:
            columns = [row[1] for row in conn.execute("pragma table_info(device_tokens)")]
            indexes = [row[1] for row in conn.execute("pragma index_list(device_tokens)")]
        self.assertIn("id", columns)
        self.assertIn("idx_device_tokens_public_id", indexes)

    def test_api_me_requires_bearer_token(self):
        httpd, base = self.run_server(StubAuthClient())
        try:
            with self.assertRaises(urllib.error.HTTPError) as cm:
                self.get_json(base + "/api/me")
            self.assertEqual(cm.exception.code, 401)
            body = json.loads(cm.exception.read().decode("utf-8"))
            self.assertEqual(body["error"], "missing bearer token")
        finally:
            self.stop_server(httpd)

    def test_create_and_download_mobile_asset_with_device_token(self):
        httpd, base = self.run_server(StubAuthClient())
        try:
            image_bytes = b"\x89PNG\r\n\x1a\nbridge-image"
            status, created = self.post_json(
                base + "/api/mobile-assets",
                {
                    "filename": "welcome.png",
                    "content_base64": base64.b64encode(image_bytes).decode("ascii"),
                },
                token="jwt-token",
            )
            self.assertEqual(status, 201)
            asset = created["asset"]
            self.assertEqual(asset["image_name"], "welcome.png")
            self.assertEqual(asset["image_mime"], "image/png")
            self.assertEqual(asset["image_sha256"], hashlib.sha256(image_bytes).hexdigest())
            self.assertEqual(asset["size"], len(image_bytes))
            self.assertTrue(asset["image_url"].endswith(f"/api/mobile-assets/{asset['id']}"))

            _, pairing = self.post_json(
                base + "/api/device-pairings",
                {"device_name": "Android"},
                token="jwt-token",
            )
            _, exchanged = self.post_json(
                base + "/api/device-pairings/exchange",
                {"code": pairing["pairing"]["code"], "device_name": "Android"},
            )

            status, headers, downloaded = self.get_bytes(
                asset["image_url"],
                token=exchanged["device_token"],
            )
            self.assertEqual(status, 200)
            self.assertEqual(headers.get_content_type(), "image/png")
            self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
            self.assertEqual(downloaded, image_bytes)
        finally:
            self.stop_server(httpd)

    def test_mobile_assets_are_isolated_by_owner_key(self):
        users = {
            "token-a": {
                "email": "a@example.com",
                "name": "A",
                "provider": "corp",
                "org_id": "org",
                "union_id": "user-a",
            },
            "token-b": {
                "email": "b@example.com",
                "name": "B",
                "provider": "corp",
                "org_id": "org",
                "union_id": "user-b",
            },
        }
        httpd, base = self.run_server(StubAuthClient(users=users))
        try:
            _, created = self.post_json(
                base + "/api/mobile-assets",
                {
                    "filename": "private.jpg",
                    "content_base64": base64.b64encode(b"private-image").decode("ascii"),
                },
                token="token-a",
            )

            with self.assertRaises(urllib.error.HTTPError) as cm:
                self.get_bytes(created["asset"]["image_url"], token="token-b")
            self.assertEqual(cm.exception.code, 404)
        finally:
            self.stop_server(httpd)

    def test_api_me_returns_current_user_and_owner_key(self):
        auth = StubAuthClient()
        httpd, base = self.run_server(auth)
        try:
            status, body = self.get_json(base + "/api/me", token="jwt-token")
            self.assertEqual(status, 200)
            self.assertIs(body["ok"], True)
            self.assertEqual(body["owner_key"], "org-1:union-1")
            self.assertEqual(body["user"]["email"], "u@example.com")
            self.assertEqual(body["user"]["departments"], ["Sales"])
            self.assertEqual(auth.tokens, ["jwt-token"])
        finally:
            self.stop_server(httpd)

    def test_api_me_rejects_invalid_token(self):
        httpd, base = self.run_server(StubAuthClient(error=PermissionError("bad token")))
        try:
            with self.assertRaises(urllib.error.HTTPError) as cm:
                self.get_json(base + "/api/me", token="bad")
            self.assertEqual(cm.exception.code, 401)
            body = json.loads(cm.exception.read().decode("utf-8"))
            self.assertEqual(body["error"], "not authenticated")
        finally:
            self.stop_server(httpd)

    def test_create_list_and_get_scrape_batch(self):
        auth = StubAuthClient()
        httpd, base = self.run_server(auth)
        try:
            payload = {
                "source": "salesforce",
                "metadata": {"scraped_at": "2026-06-22T10:00:00"},
                "customers": [
                    {"name": "Alice", "phone": "123"},
                    {"name": "Bob", "phone": "456"},
                ],
                "owner_key": "attacker-controlled",
            }
            status, created = self.post_json(base + "/api/scrape-batches", payload, token="jwt-token")
            self.assertEqual(status, 201)
            batch = created["batch"]
            self.assertEqual(batch["source"], "salesforce")
            self.assertEqual(batch["customer_count"], 2)
            self.assertNotIn("customers", batch)
            batch_id = batch["id"]

            status, listed = self.get_json(base + "/api/scrape-batches", token="jwt-token")
            self.assertEqual(status, 200)
            self.assertEqual(len(listed["batches"]), 1)
            self.assertEqual(listed["batches"][0]["id"], batch_id)
            self.assertEqual(listed["batches"][0]["customer_count"], 2)

            status, detail = self.get_json(base + f"/api/scrape-batches/{batch_id}", token="jwt-token")
            self.assertEqual(status, 200)
            self.assertEqual(detail["batch"]["customers"][0]["name"], "Alice")
        finally:
            self.stop_server(httpd)

    def test_scrape_batches_are_isolated_by_owner_key(self):
        users = {
            "token-a": {
                "email": "a@example.com",
                "name": "A",
                "provider": "corp",
                "org_id": "org",
                "union_id": "user-a",
            },
            "token-b": {
                "email": "b@example.com",
                "name": "B",
                "provider": "corp",
                "org_id": "org",
                "union_id": "user-b",
            },
        }
        httpd, base = self.run_server(StubAuthClient(users=users))
        try:
            _, created = self.post_json(
                base + "/api/scrape-batches",
                {"customers": [{"name": "Private"}]},
                token="token-a",
            )
            batch_id = created["batch"]["id"]

            _, listed_a = self.get_json(base + "/api/scrape-batches", token="token-a")
            _, listed_b = self.get_json(base + "/api/scrape-batches", token="token-b")
            self.assertEqual(len(listed_a["batches"]), 1)
            self.assertEqual(listed_b["batches"], [])

            with self.assertRaises(urllib.error.HTTPError) as cm:
                self.get_json(base + f"/api/scrape-batches/{batch_id}", token="token-b")
            self.assertEqual(cm.exception.code, 404)
        finally:
            self.stop_server(httpd)

    def test_create_scrape_batch_validates_customers_array(self):
        httpd, base = self.run_server(StubAuthClient())
        try:
            with self.assertRaises(urllib.error.HTTPError) as cm:
                self.post_json(base + "/api/scrape-batches", {"customers": "bad"}, token="jwt-token")
            self.assertEqual(cm.exception.code, 400)
            body = json.loads(cm.exception.read().decode("utf-8"))
            self.assertEqual(body["error"], "customers must be an array")
        finally:
            self.stop_server(httpd)

    def test_create_list_get_and_update_send_task(self):
        httpd, base = self.run_server(StubAuthClient())
        try:
            _, created_batch = self.post_json(
                base + "/api/scrape-batches",
                {
                    "source": "salesforce",
                    "customers": [
                        {"name": "Alice", "phone": "123"},
                        {"name": "Bob", "phone": "456"},
                    ],
                },
                token="jwt-token",
            )
            batch_id = created_batch["batch"]["id"]

            status, created_task = self.post_json(
                base + "/api/send-tasks",
                {
                    "scrape_batch_id": batch_id,
                    "channel": "wecom",
                    "message_template": "Hello {{name}}",
                    "metadata": {"created_by": "desktop"},
                    "owner_key": "attacker-controlled",
                },
                token="jwt-token",
            )
            self.assertEqual(status, 201)
            task = created_task["task"]
            self.assertEqual(task["scrape_batch_id"], batch_id)
            self.assertEqual(task["source"], "salesforce")
            self.assertEqual(task["channel"], "wecom")
            self.assertEqual(task["message_template"], "Hello {{name}}")
            self.assertEqual(task["item_count"], 2)
            self.assertEqual(task["pending_count"], 2)
            self.assertEqual(task["success_count"], 0)
            self.assertEqual(task["status"], "pending")
            task_id = task["id"]

            status, listed = self.get_json(base + "/api/send-tasks", token="jwt-token")
            self.assertEqual(status, 200)
            self.assertEqual(len(listed["tasks"]), 1)
            self.assertEqual(listed["tasks"][0]["id"], task_id)
            self.assertNotIn("items", listed["tasks"][0])

            status, detail = self.get_json(base + f"/api/send-tasks/{task_id}", token="jwt-token")
            self.assertEqual(status, 200)
            self.assertEqual(len(detail["task"]["items"]), 2)
            first_item = detail["task"]["items"][0]
            self.assertEqual(first_item["customer"]["name"], "Alice")
            self.assertEqual(first_item["status"], "pending")

            status, updated = self.post_json(
                base + f"/api/send-task-items/{first_item['id']}/result",
                {
                    "status": "success",
                    "result": {"sent_at": "2026-06-22T10:30:00Z"},
                },
                token="jwt-token",
            )
            self.assertEqual(status, 200)
            self.assertEqual(updated["item"]["status"], "success")
            self.assertEqual(updated["task"]["status"], "in_progress")
            self.assertEqual(updated["task"]["pending_count"], 1)
            self.assertEqual(updated["task"]["success_count"], 1)
        finally:
            self.stop_server(httpd)

    def test_claim_next_send_task_item(self):
        httpd, base = self.run_server(StubAuthClient())
        try:
            _, created_batch = self.post_json(
                base + "/api/scrape-batches",
                {"customers": [{"name": "Alice"}, {"name": "Bob"}]},
                token="jwt-token",
            )
            _, created_task = self.post_json(
                base + "/api/send-tasks",
                {"scrape_batch_id": created_batch["batch"]["id"]},
                token="jwt-token",
            )
            task_id = created_task["task"]["id"]

            status, claimed = self.post_json(
                base + f"/api/send-tasks/{task_id}/claim-next",
                {"worker_id": "android-1"},
                token="jwt-token",
            )
            self.assertEqual(status, 200)
            self.assertEqual(claimed["item"]["customer"]["name"], "Alice")
            self.assertEqual(claimed["item"]["status"], "in_progress")
            self.assertEqual(claimed["item"]["result"]["worker_id"], "android-1")
            self.assertEqual(claimed["task"]["status"], "in_progress")
            self.assertEqual(claimed["task"]["pending_count"], 1)

            _, second = self.post_json(
                base + f"/api/send-tasks/{task_id}/claim-next",
                {},
                token="jwt-token",
            )
            self.assertEqual(second["item"]["customer"]["name"], "Bob")
            self.assertEqual(second["task"]["pending_count"], 0)

            _, empty = self.post_json(
                base + f"/api/send-tasks/{task_id}/claim-next",
                {},
                token="jwt-token",
            )
            self.assertIsNone(empty["item"])
            self.assertEqual(empty["task"]["pending_count"], 0)
        finally:
            self.stop_server(httpd)

    def test_device_pairing_token_can_consume_send_tasks(self):
        httpd, base = self.run_server(StubAuthClient())
        try:
            _, created_batch = self.post_json(
                base + "/api/scrape-batches",
                {"customers": [{"name": "Alice"}, {"name": "Bob"}]},
                token="jwt-token",
            )
            _, created_task = self.post_json(
                base + "/api/send-tasks",
                {"scrape_batch_id": created_batch["batch"]["id"]},
                token="jwt-token",
            )
            task_id = created_task["task"]["id"]

            status, pairing = self.post_json(
                base + "/api/device-pairings",
                {"device_name": "测试手机"},
                token="jwt-token",
            )
            self.assertEqual(status, 201)
            code = pairing["pairing"]["code"]
            self.assertRegex(code, r"^\d{3}-\d{3}$")

            status, exchanged = self.post_json(
                base + "/api/device-pairings/exchange",
                {"code": code, "device_name": "Android", "device_id": "device-1"},
            )
            self.assertEqual(status, 200)
            device_token = exchanged["device_token"]
            self.assertTrue(device_token.startswith(bridge_server.DEVICE_TOKEN_PREFIX))
            self.assertEqual(exchanged["paired_user"]["name"], "User")
            self.assertEqual(exchanged["paired_user"]["email"], "u@example.com")

            status, listed = self.get_json(base + "/api/send-tasks", token=device_token)
            self.assertEqual(status, 200)
            self.assertEqual(listed["tasks"][0]["id"], task_id)

            status, claimed = self.post_json(
                base + f"/api/send-tasks/{task_id}/claim-next",
                {"worker_id": "android-1"},
                token=device_token,
            )
            self.assertEqual(status, 200)
            self.assertEqual(claimed["item"]["customer"]["name"], "Alice")

            status, updated = self.post_json(
                base + f"/api/send-task-items/{claimed['item']['id']}/result",
                {"status": "success", "result": {"mode": "test"}},
                token=device_token,
            )
            self.assertEqual(status, 200)
            self.assertEqual(updated["item"]["status"], "success")
        finally:
            self.stop_server(httpd)

    def test_device_pairing_code_is_single_use_and_device_cannot_create_tasks(self):
        httpd, base = self.run_server(StubAuthClient())
        try:
            _, pairing = self.post_json(
                base + "/api/device-pairings",
                {},
                token="jwt-token",
            )
            code = pairing["pairing"]["code"]
            _, exchanged = self.post_json(
                base + "/api/device-pairings/exchange",
                {"code": code},
            )
            device_token = exchanged["device_token"]

            with self.assertRaises(urllib.error.HTTPError) as cm:
                self.post_json(base + "/api/device-pairings/exchange", {"code": code})
            self.assertEqual(cm.exception.code, 401)

            with self.assertRaises(urllib.error.HTTPError) as cm:
                self.post_json(
                    base + "/api/send-tasks",
                    {"scrape_batch_id": "batch-id"},
                    token=device_token,
                )
            self.assertEqual(cm.exception.code, 401)
        finally:
            self.stop_server(httpd)

    def test_user_can_list_and_revoke_paired_devices(self):
        httpd, base = self.run_server(StubAuthClient())
        try:
            _, pairing = self.post_json(
                base + "/api/device-pairings",
                {"device_name": "测试手机"},
                token="jwt-token",
            )
            _, exchanged = self.post_json(
                base + "/api/device-pairings/exchange",
                {
                    "code": pairing["pairing"]["code"],
                    "device_name": "Android",
                    "device_id": "android-device-1",
                },
            )
            device_token = exchanged["device_token"]

            status, listed = self.get_json(base + "/api/devices", token="jwt-token")
            self.assertEqual(status, 200)
            self.assertEqual(len(listed["devices"]), 1)
            device = listed["devices"][0]
            self.assertTrue(device["id"].startswith("dev_"))
            self.assertEqual(device["device_name"], "Android")
            self.assertEqual(device["device_id"], "android-device-1")
            self.assertTrue(device["created_at"])
            self.assertTrue(device["last_seen_at"])
            self.assertEqual(device["revoked_at"], "")

            status, revoked = self.post_json(
                base + f"/api/devices/{device['id']}/revoke",
                {},
                token="jwt-token",
            )
            self.assertEqual(status, 200)
            self.assertEqual(revoked["device"]["id"], device["id"])
            self.assertTrue(revoked["device"]["revoked_at"])

            with self.assertRaises(urllib.error.HTTPError) as cm:
                self.get_json(base + "/api/send-tasks", token=device_token)
            self.assertEqual(cm.exception.code, 401)
        finally:
            self.stop_server(httpd)

    def test_repairing_same_device_replaces_old_active_binding(self):
        httpd, base = self.run_server(StubAuthClient())
        try:
            _, first_pairing = self.post_json(
                base + "/api/device-pairings",
                {"device_name": "测试手机"},
                token="jwt-token",
            )
            _, first_exchange = self.post_json(
                base + "/api/device-pairings/exchange",
                {
                    "code": first_pairing["pairing"]["code"],
                    "device_name": "Android old",
                    "device_id": "android-device-1",
                },
            )
            old_device_token = first_exchange["device_token"]

            _, second_pairing = self.post_json(
                base + "/api/device-pairings",
                {"device_name": "测试手机"},
                token="jwt-token",
            )
            _, second_exchange = self.post_json(
                base + "/api/device-pairings/exchange",
                {
                    "code": second_pairing["pairing"]["code"],
                    "device_name": "Android new",
                    "device_id": "android-device-1",
                },
            )
            new_device_token = second_exchange["device_token"]

            status, listed = self.get_json(base + "/api/devices", token="jwt-token")
            self.assertEqual(status, 200)
            self.assertEqual(len(listed["devices"]), 1)
            self.assertEqual(listed["devices"][0]["device_name"], "Android new")
            self.assertEqual(listed["devices"][0]["device_id"], "android-device-1")
            self.assertEqual(listed["devices"][0]["revoked_at"], "")

            with self.assertRaises(urllib.error.HTTPError) as cm:
                self.get_json(base + "/api/send-tasks", token=old_device_token)
            self.assertEqual(cm.exception.code, 401)

            status, tasks = self.get_json(base + "/api/send-tasks", token=new_device_token)
            self.assertEqual(status, 200)
            self.assertIn("tasks", tasks)
        finally:
            self.stop_server(httpd)

    def test_device_token_cannot_manage_devices_and_revoke_is_idempotent(self):
        httpd, base = self.run_server(StubAuthClient())
        try:
            _, pairing = self.post_json(base + "/api/device-pairings", {}, token="jwt-token")
            _, exchanged = self.post_json(
                base + "/api/device-pairings/exchange",
                {"code": pairing["pairing"]["code"], "device_name": "Android"},
            )
            device_token = exchanged["device_token"]
            _, listed = self.get_json(base + "/api/devices", token="jwt-token")
            device_id = listed["devices"][0]["id"]

            with self.assertRaises(urllib.error.HTTPError) as cm:
                self.get_json(base + "/api/devices", token=device_token)
            self.assertEqual(cm.exception.code, 401)

            with self.assertRaises(urllib.error.HTTPError) as cm:
                self.post_json(base + f"/api/devices/{device_id}/revoke", {}, token=device_token)
            self.assertEqual(cm.exception.code, 401)

            status, first = self.post_json(base + f"/api/devices/{device_id}/revoke", {}, token="jwt-token")
            self.assertEqual(status, 200)
            status, second = self.post_json(base + f"/api/devices/{device_id}/revoke", {}, token="jwt-token")
            self.assertEqual(status, 200)
            self.assertEqual(first["device"]["id"], second["device"]["id"])
            self.assertTrue(second["device"]["revoked_at"])
        finally:
            self.stop_server(httpd)

    def test_send_tasks_are_isolated_by_owner_key(self):
        users = {
            "token-a": {
                "email": "a@example.com",
                "name": "A",
                "provider": "corp",
                "org_id": "org",
                "union_id": "user-a",
            },
            "token-b": {
                "email": "b@example.com",
                "name": "B",
                "provider": "corp",
                "org_id": "org",
                "union_id": "user-b",
            },
        }
        httpd, base = self.run_server(StubAuthClient(users=users))
        try:
            _, created_batch = self.post_json(
                base + "/api/scrape-batches",
                {"customers": [{"name": "Private"}]},
                token="token-a",
            )
            batch_id = created_batch["batch"]["id"]
            _, created_task = self.post_json(
                base + "/api/send-tasks",
                {"scrape_batch_id": batch_id},
                token="token-a",
            )
            task_id = created_task["task"]["id"]

            _, listed_a = self.get_json(base + "/api/send-tasks", token="token-a")
            _, listed_b = self.get_json(base + "/api/send-tasks", token="token-b")
            self.assertEqual(len(listed_a["tasks"]), 1)
            self.assertEqual(listed_b["tasks"], [])

            with self.assertRaises(urllib.error.HTTPError) as cm:
                self.post_json(
                    base + "/api/send-tasks",
                    {"scrape_batch_id": batch_id},
                    token="token-b",
                )
            self.assertEqual(cm.exception.code, 404)

            with self.assertRaises(urllib.error.HTTPError) as cm:
                self.get_json(base + f"/api/send-tasks/{task_id}", token="token-b")
            self.assertEqual(cm.exception.code, 404)

            with self.assertRaises(urllib.error.HTTPError) as cm:
                self.post_json(
                    base + f"/api/send-tasks/{task_id}/claim-next",
                    {},
                    token="token-b",
                )
            self.assertEqual(cm.exception.code, 404)
        finally:
            self.stop_server(httpd)

    def test_create_send_task_validates_batch_id_and_metadata(self):
        httpd, base = self.run_server(StubAuthClient())
        try:
            with self.assertRaises(urllib.error.HTTPError) as cm:
                self.post_json(base + "/api/send-tasks", {}, token="jwt-token")
            self.assertEqual(cm.exception.code, 400)
            body = json.loads(cm.exception.read().decode("utf-8"))
            self.assertEqual(body["error"], "scrape_batch_id is required")

            with self.assertRaises(urllib.error.HTTPError) as cm:
                self.post_json(
                    base + "/api/send-tasks",
                    {"scrape_batch_id": "missing", "metadata": []},
                    token="jwt-token",
                )
            self.assertEqual(cm.exception.code, 400)
            body = json.loads(cm.exception.read().decode("utf-8"))
            self.assertEqual(body["error"], "metadata must be an object")
        finally:
            self.stop_server(httpd)

    def test_update_send_task_item_result_validates_status(self):
        httpd, base = self.run_server(StubAuthClient())
        try:
            _, created_batch = self.post_json(
                base + "/api/scrape-batches",
                {"customers": [{"name": "Alice"}]},
                token="jwt-token",
            )
            _, created_task = self.post_json(
                base + "/api/send-tasks",
                {"scrape_batch_id": created_batch["batch"]["id"]},
                token="jwt-token",
            )
            _, detail = self.get_json(
                base + f"/api/send-tasks/{created_task['task']['id']}",
                token="jwt-token",
            )
            item_id = detail["task"]["items"][0]["id"]

            with self.assertRaises(urllib.error.HTTPError) as cm:
                self.post_json(
                    base + f"/api/send-task-items/{item_id}/result",
                    {"status": "unknown"},
                    token="jwt-token",
                )
            self.assertEqual(cm.exception.code, 400)
            body = json.loads(cm.exception.read().decode("utf-8"))
            self.assertEqual(
                body["error"],
                "status must be one of pending, success, failed, skipped",
            )
        finally:
            self.stop_server(httpd)


if __name__ == "__main__":
    unittest.main()
