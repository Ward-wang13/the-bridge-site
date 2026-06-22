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


if __name__ == "__main__":
    unittest.main()
