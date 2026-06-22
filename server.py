#!/usr/bin/env python3
"""The Bridge static site plus authenticated API.

This intentionally uses only the Python standard library so the long-running
TAE image stays small. User data APIs must go through auth-gateway userinfo
validation and derive ownership on the server side.
"""

from __future__ import annotations

import json
import mimetypes
import os
import posixpath
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from contextlib import closing
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parent
STATIC_DIR = Path(os.environ.get("THEBRIDGE_STATIC_DIR", ROOT_DIR)).resolve()
DATA_ROOT = Path(os.environ.get("THEBRIDGE_DATA_ROOT", "/data/thebridge")).resolve()
DB_PATH = Path(os.environ.get("THEBRIDGE_DB_PATH", DATA_ROOT / "cloud" / "thebridge.db")).resolve()
AUTH_GATEWAY_BASE_URL = os.environ.get(
    "AUTH_GATEWAY_BASE_URL",
    "https://auth-gateway.truesightai.com",
).rstrip("/")
AUTH_USERINFO_TIMEOUT = float(os.environ.get("AUTH_USERINFO_TIMEOUT", "10"))
PORT = int(os.environ.get("PORT", "80"))

OWNER_FALLBACK_KEYS = ("union_id", "email", "sub", "id")


def extract_bearer_token(header_value: str | None) -> str:
    if not header_value:
        return ""
    scheme, _, value = header_value.strip().partition(" ")
    if scheme.lower() != "bearer" or not value.strip():
        return ""
    return value.strip()


def derive_owner_key(user: dict[str, Any]) -> str:
    org_id = str(user.get("org_id") or "default").strip() or "default"
    union_id = str(user.get("union_id") or "").strip()
    if union_id:
        return f"{org_id}:{union_id}"

    for key in OWNER_FALLBACK_KEYS:
        value = str(user.get(key) or "").strip()
        if value:
            return f"{org_id}:{key}:{value}"

    provider = str(user.get("provider") or "unknown").strip() or "unknown"
    provider_user_id = str(
        user.get("provider_user_id")
        or user.get("provider_id")
        or user.get("uid")
        or ""
    ).strip()
    if provider_user_id:
        return f"{org_id}:{provider}:{provider_user_id}"

    raise ValueError("userinfo 缺少可用于数据隔离的稳定用户标识")


def public_user(user: dict[str, Any]) -> dict[str, Any]:
    return {
        "email": user.get("email") or "",
        "name": user.get("name") or "",
        "provider": user.get("provider") or "",
        "org_id": user.get("org_id") or "",
        "union_id": user.get("union_id") or "",
        "departments": user.get("departments") or [],
    }


class AuthGatewayClient:
    def __init__(self, base_url: str = AUTH_GATEWAY_BASE_URL, timeout: float = AUTH_USERINFO_TIMEOUT):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def userinfo(self, token: str) -> dict[str, Any]:
        req = urllib.request.Request(
            f"{self.base_url}/userinfo",
            headers={"Authorization": f"Bearer {token}"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                if getattr(resp, "status", 200) != HTTPStatus.OK:
                    raise PermissionError("not authenticated")
                payload = resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            if exc.code == HTTPStatus.UNAUTHORIZED:
                raise PermissionError("not authenticated") from exc
            raise RuntimeError(f"auth-gateway /userinfo HTTP {exc.code}") from exc
        data = json.loads(payload)
        if not isinstance(data, dict):
            raise RuntimeError("auth-gateway /userinfo returned non-object JSON")
        return data


class Storage:
    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_db(self) -> None:
        with closing(self._connect()) as conn:
            conn.executescript(
                """
                create table if not exists users(
                  owner_key text primary key,
                  org_id text,
                  union_id text,
                  email text,
                  name text,
                  provider text,
                  user_json text not null,
                  first_seen_at text not null,
                  last_seen_at text not null
                );

                create table if not exists scrape_batches(
                  id text primary key,
                  owner_key text not null,
                  source text not null,
                  created_at text not null,
                  status text not null,
                  metadata_json text not null,
                  customers_json text not null,
                  customer_count integer not null
                );

                create index if not exists idx_scrape_batches_owner_created
                  on scrape_batches(owner_key, created_at desc);
                """
            )
            conn.commit()

    def touch_user(self, owner_key: str, user: dict[str, Any]) -> None:
        now = now_iso()
        with closing(self._connect()) as conn:
            conn.execute(
                """
                insert into users(
                  owner_key, org_id, union_id, email, name, provider, user_json,
                  first_seen_at, last_seen_at
                )
                values (?, ?, ?, ?, ?, ?, ?, ?, ?)
                on conflict(owner_key) do update set
                  org_id = excluded.org_id,
                  union_id = excluded.union_id,
                  email = excluded.email,
                  name = excluded.name,
                  provider = excluded.provider,
                  user_json = excluded.user_json,
                  last_seen_at = excluded.last_seen_at
                """,
                (
                    owner_key,
                    user.get("org_id") or "",
                    user.get("union_id") or "",
                    user.get("email") or "",
                    user.get("name") or "",
                    user.get("provider") or "",
                    json.dumps(user, ensure_ascii=False),
                    now,
                    now,
                ),
            )
            conn.commit()

    def create_scrape_batch(
        self,
        owner_key: str,
        source: str,
        customers: list[dict[str, Any]],
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        batch = {
            "id": uuid.uuid4().hex,
            "owner_key": owner_key,
            "source": source,
            "created_at": now_iso(),
            "status": "stored",
            "metadata": metadata or {},
            "customers": customers,
            "customer_count": len(customers),
        }
        with closing(self._connect()) as conn:
            conn.execute(
                """
                insert into scrape_batches(
                  id, owner_key, source, created_at, status, metadata_json,
                  customers_json, customer_count
                )
                values (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    batch["id"],
                    owner_key,
                    source,
                    batch["created_at"],
                    batch["status"],
                    json.dumps(batch["metadata"], ensure_ascii=False),
                    json.dumps(customers, ensure_ascii=False),
                    batch["customer_count"],
                ),
            )
            conn.commit()
        return batch

    def list_scrape_batches(self, owner_key: str) -> list[dict[str, Any]]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                """
                select id, source, created_at, status, metadata_json, customer_count
                from scrape_batches
                where owner_key = ?
                order by created_at desc
                """,
                (owner_key,),
            ).fetchall()
        return [row_to_batch_summary(row) for row in rows]

    def get_scrape_batch(self, owner_key: str, batch_id: str) -> dict[str, Any] | None:
        with closing(self._connect()) as conn:
            row = conn.execute(
                """
                select id, source, created_at, status, metadata_json,
                       customers_json, customer_count
                from scrape_batches
                where owner_key = ? and id = ?
                """,
                (owner_key, batch_id),
            ).fetchone()
        if row is None:
            return None
        return row_to_batch_detail(row)


class BridgeHandler(BaseHTTPRequestHandler):
    server_version = "TheBridgeSite/0.1"
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:  # noqa: N802 - stdlib method name
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/health":
            self.send_json({"ok": True, "time": int(time.time())})
            return
        if parsed.path == "/api/me":
            self.handle_me()
            return
        if parsed.path == "/api/scrape-batches":
            self.handle_list_scrape_batches()
            return
        if parsed.path.startswith("/api/scrape-batches/"):
            self.handle_get_scrape_batch(parsed.path.removeprefix("/api/scrape-batches/"))
            return
        if parsed.path.startswith("/api/"):
            self.send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            return
        self.serve_static(parsed.path)

    def do_POST(self) -> None:  # noqa: N802 - stdlib method name
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/scrape-batches":
            self.handle_create_scrape_batch()
            return
        if parsed.path.startswith("/api/"):
            self.send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            return
        self.send_json({"error": "method not allowed"}, HTTPStatus.METHOD_NOT_ALLOWED)

    def handle_me(self) -> None:
        ctx = self.require_user()
        if ctx is None:
            return
        self.server.storage.touch_user(ctx["owner_key"], ctx["user"])  # type: ignore[attr-defined]
        self.send_json({
            "ok": True,
            "owner_key": ctx["owner_key"],
            "user": public_user(ctx["user"]),
        })

    def handle_create_scrape_batch(self) -> None:
        ctx = self.require_user()
        if ctx is None:
            return
        body = self.read_json_body()
        if body is None:
            return
        source = str(body.get("source") or "salesforce").strip() or "salesforce"
        customers = body.get("customers")
        metadata = body.get("metadata") or {}
        if not isinstance(customers, list):
            self.send_json({"error": "customers must be an array"}, HTTPStatus.BAD_REQUEST)
            return
        if not all(isinstance(customer, dict) for customer in customers):
            self.send_json({"error": "customers must contain objects"}, HTTPStatus.BAD_REQUEST)
            return
        if not isinstance(metadata, dict):
            self.send_json({"error": "metadata must be an object"}, HTTPStatus.BAD_REQUEST)
            return
        self.server.storage.touch_user(ctx["owner_key"], ctx["user"])  # type: ignore[attr-defined]
        batch = self.server.storage.create_scrape_batch(  # type: ignore[attr-defined]
            ctx["owner_key"],
            source,
            customers,
            metadata,
        )
        response = dict(batch)
        response.pop("customers", None)
        self.send_json({"ok": True, "batch": response}, HTTPStatus.CREATED)

    def handle_list_scrape_batches(self) -> None:
        ctx = self.require_user()
        if ctx is None:
            return
        self.server.storage.touch_user(ctx["owner_key"], ctx["user"])  # type: ignore[attr-defined]
        batches = self.server.storage.list_scrape_batches(ctx["owner_key"])  # type: ignore[attr-defined]
        self.send_json({"ok": True, "batches": batches})

    def handle_get_scrape_batch(self, raw_batch_id: str) -> None:
        ctx = self.require_user()
        if ctx is None:
            return
        batch_id = urllib.parse.unquote(raw_batch_id).strip("/")
        if not batch_id:
            self.send_json({"error": "missing batch id"}, HTTPStatus.BAD_REQUEST)
            return
        batch = self.server.storage.get_scrape_batch(ctx["owner_key"], batch_id)  # type: ignore[attr-defined]
        if batch is None:
            self.send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            return
        self.send_json({"ok": True, "batch": batch})

    def require_user(self) -> dict[str, Any] | None:
        token = extract_bearer_token(self.headers.get("Authorization"))
        if not token:
            self.send_json({"error": "missing bearer token"}, HTTPStatus.UNAUTHORIZED)
            return None
        try:
            user = self.server.auth_client.userinfo(token)  # type: ignore[attr-defined]
            owner_key = derive_owner_key(user)
        except PermissionError:
            self.send_json({"error": "not authenticated"}, HTTPStatus.UNAUTHORIZED)
            return None
        except Exception as exc:
            self.send_json({"error": "auth failed", "detail": str(exc)}, HTTPStatus.BAD_GATEWAY)
            return None
        return {"user": user, "owner_key": owner_key}

    def read_json_body(self) -> dict[str, Any] | None:
        length_raw = self.headers.get("Content-Length") or "0"
        try:
            length = int(length_raw)
        except ValueError:
            self.send_json({"error": "invalid content length"}, HTTPStatus.BAD_REQUEST)
            return None
        if length <= 0:
            self.send_json({"error": "missing JSON body"}, HTTPStatus.BAD_REQUEST)
            return None
        if length > 20 * 1024 * 1024:
            self.send_json({"error": "request body too large"}, HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
            return None
        try:
            data = json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            self.send_json({"error": "invalid JSON body"}, HTTPStatus.BAD_REQUEST)
            return None
        if not isinstance(data, dict):
            self.send_json({"error": "JSON body must be an object"}, HTTPStatus.BAD_REQUEST)
            return None
        return data

    def serve_static(self, request_path: str) -> None:
        if request_path.startswith("/resources/"):
            base_dir = DATA_ROOT / "resources"
            rel_path = request_path.removeprefix("/resources/")
            cache_control = "public, max-age=300"
        elif request_path.startswith("/updates/"):
            base_dir = DATA_ROOT / "updates"
            rel_path = request_path.removeprefix("/updates/")
            cache_control = "public, max-age=60"
        else:
            base_dir = STATIC_DIR
            rel_path = request_path.lstrip("/") or "index.html"
            cache_control = ""

        safe_path = safe_join(base_dir, rel_path)
        if safe_path is None or not safe_path.exists() or safe_path.is_dir():
            if base_dir == STATIC_DIR:
                safe_path = STATIC_DIR / "index.html"
            else:
                self.send_error(HTTPStatus.NOT_FOUND)
                return

        ctype = mimetypes.guess_type(str(safe_path))[0] or "application/octet-stream"
        try:
            content = safe_path.read_bytes()
        except OSError:
            self.send_error(HTTPStatus.NOT_FOUND)
            return

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("X-Content-Type-Options", "nosniff")
        if cache_control:
            self.send_header("Cache-Control", cache_control)
        self.end_headers()
        self.wfile.write(content)

    def send_json(self, data: dict[str, Any], status: int = HTTPStatus.OK) -> None:
        content = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if status >= HTTPStatus.BAD_REQUEST:
            self.send_header("Connection", "close")
            self.close_connection = True
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, fmt: str, *args: Any) -> None:
        # Avoid logging Authorization headers or auth codes; keep default access
        # log compact enough for TAE diagnostics.
        print("%s - - [%s] %s" % (self.address_string(), self.log_date_time_string(), fmt % args))


def safe_join(base_dir: Path, rel_path: str) -> Path | None:
    rel_path = urllib.parse.unquote(rel_path)
    rel_path = posixpath.normpath("/" + rel_path).lstrip("/")
    candidate = (base_dir / rel_path).resolve()
    try:
        candidate.relative_to(base_dir.resolve())
    except ValueError:
        return None
    return candidate


class BridgeHTTPServer(ThreadingHTTPServer):
    auth_client: AuthGatewayClient
    storage: Storage


def make_server(
    port: int = PORT,
    auth_client: AuthGatewayClient | None = None,
    storage: Storage | None = None,
) -> BridgeHTTPServer:
    server = BridgeHTTPServer(("", port), BridgeHandler)
    server.auth_client = auth_client or AuthGatewayClient()
    server.storage = storage or Storage()
    return server


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def row_to_batch_summary(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "source": row["source"],
        "created_at": row["created_at"],
        "status": row["status"],
        "metadata": json.loads(row["metadata_json"] or "{}"),
        "customer_count": row["customer_count"],
    }


def row_to_batch_detail(row: sqlite3.Row) -> dict[str, Any]:
    batch = row_to_batch_summary(row)
    batch["customers"] = json.loads(row["customers_json"] or "[]")
    return batch


def main() -> None:
    server = make_server()
    print(f"The Bridge site listening on :{PORT}")
    print(f"Static dir: {STATIC_DIR}")
    print(f"Data root: {DATA_ROOT}")
    print(f"Database: {DB_PATH}")
    print(f"Auth gateway: {AUTH_GATEWAY_BASE_URL}")
    server.serve_forever()


if __name__ == "__main__":
    main()
