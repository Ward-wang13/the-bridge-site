from __future__ import annotations

import json
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
