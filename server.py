#!/usr/bin/env python3
"""The Bridge static site plus authenticated API.

This intentionally uses only the Python standard library so the long-running
TAE image stays small. User data APIs must go through auth-gateway userinfo
validation and derive ownership on the server side.
"""

from __future__ import annotations

import json
import base64
import hashlib
import mimetypes
import os
import posixpath
import secrets
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
DATA_SUBDIRS = ("resources", "updates", "cloud")
DEVICE_TOKEN_PREFIX = "tbdev_"
PAIRING_CODE_TTL_SECONDS = int(os.environ.get("PAIRING_CODE_TTL_SECONDS", "600"))
MOBILE_ASSET_MAX_BYTES = int(os.environ.get("MOBILE_ASSET_MAX_BYTES", str(10 * 1024 * 1024)))
MOBILE_ASSET_MIME_BY_EXT = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
}


def ensure_data_directories(data_root: Path = DATA_ROOT) -> None:
    for subdir in DATA_SUBDIRS:
        (data_root / subdir).mkdir(parents=True, exist_ok=True)


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


def hash_secret(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def normalize_pairing_code(value: str) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def format_pairing_code(value: str) -> str:
    clean = normalize_pairing_code(value)
    return f"{clean[:3]}-{clean[3:]}" if len(clean) == 6 else clean


def new_device_public_id() -> str:
    return "dev_" + secrets.token_urlsafe(12)


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
    def __init__(self, db_path: Path = DB_PATH, data_root: Path | None = None):
        self.db_path = db_path
        root = data_root or (DATA_ROOT if db_path == DB_PATH else db_path.parent)
        self.data_root = root
        self.mobile_assets_dir = root / "cloud" / "mobile-assets"
        ensure_data_directories(root)
        self.mobile_assets_dir.mkdir(parents=True, exist_ok=True)
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

                create table if not exists send_tasks(
                  id text primary key,
                  owner_key text not null,
                  scrape_batch_id text not null,
                  source text not null,
                  channel text not null,
                  message_template text not null,
                  status text not null,
                  created_at text not null,
                  updated_at text not null,
                  metadata_json text not null,
                  item_count integer not null,
                  pending_count integer not null,
                  success_count integer not null,
                  failed_count integer not null,
                  skipped_count integer not null,
                  foreign key(scrape_batch_id) references scrape_batches(id)
                );

                create index if not exists idx_send_tasks_owner_created
                  on send_tasks(owner_key, created_at desc);

                create table if not exists send_task_items(
                  id text primary key,
                  task_id text not null,
                  owner_key text not null,
                  scrape_batch_id text not null,
                  customer_index integer not null,
                  customer_json text not null,
                  status text not null,
                  created_at text not null,
                  updated_at text not null,
                  result_json text not null,
                  last_error text not null,
                  foreign key(task_id) references send_tasks(id) on delete cascade
                );

                create index if not exists idx_send_task_items_task
                  on send_task_items(task_id, customer_index);

                create index if not exists idx_send_task_items_owner_status
                  on send_task_items(owner_key, status, created_at);

                create table if not exists device_pairing_codes(
                  code_hash text primary key,
                  owner_key text not null,
                  device_name text not null,
                  created_at text not null,
                  expires_at integer not null,
                  claimed_at text,
                  claimed_by text
                );

                create index if not exists idx_device_pairing_codes_owner
                  on device_pairing_codes(owner_key, created_at desc);

                create table if not exists device_tokens(
                  token_hash text primary key,
                  id text,
                  owner_key text not null,
                  device_name text not null,
                  device_id text not null,
                  created_at text not null,
                  last_seen_at text not null,
                  revoked_at text
                );

                create index if not exists idx_device_tokens_owner
                  on device_tokens(owner_key, created_at desc);

                create unique index if not exists idx_device_tokens_public_id
                  on device_tokens(id);

                create table if not exists mobile_assets(
                  id text primary key,
                  owner_key text not null,
                  filename text not null,
                  mime text not null,
                  sha256 text not null,
                  size integer not null,
                  created_at text not null,
                  rel_path text not null
                );

                create index if not exists idx_mobile_assets_owner_created
                  on mobile_assets(owner_key, created_at desc);
                """
            )
            try:
                conn.execute("alter table device_tokens add column id text")
            except sqlite3.OperationalError:
                pass
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

    def create_send_task_from_batch(
        self,
        owner_key: str,
        scrape_batch_id: str,
        channel: str,
        message_template: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        now = now_iso()
        task_id = uuid.uuid4().hex
        with closing(self._connect()) as conn:
            batch = conn.execute(
                """
                select id, source, customers_json
                from scrape_batches
                where owner_key = ? and id = ?
                """,
                (owner_key, scrape_batch_id),
            ).fetchone()
            if batch is None:
                return None
            customers = json.loads(batch["customers_json"] or "[]")
            if not isinstance(customers, list):
                customers = []
            item_count = len(customers)
            conn.execute(
                """
                insert into send_tasks(
                  id, owner_key, scrape_batch_id, source, channel,
                  message_template, status, created_at, updated_at,
                  metadata_json, item_count, pending_count, success_count,
                  failed_count, skipped_count
                )
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    owner_key,
                    scrape_batch_id,
                    batch["source"],
                    channel,
                    message_template,
                    "pending" if item_count else "empty",
                    now,
                    now,
                    json.dumps(metadata or {}, ensure_ascii=False),
                    item_count,
                    item_count,
                    0,
                    0,
                    0,
                ),
            )
            conn.executemany(
                """
                insert into send_task_items(
                  id, task_id, owner_key, scrape_batch_id, customer_index,
                  customer_json, status, created_at, updated_at,
                  result_json, last_error
                )
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        uuid.uuid4().hex,
                        task_id,
                        owner_key,
                        scrape_batch_id,
                        index,
                        json.dumps(customer, ensure_ascii=False),
                        "pending",
                        now,
                        now,
                        "{}",
                        "",
                    )
                    for index, customer in enumerate(customers)
                ],
            )
            conn.commit()
            return self._get_send_task_summary(conn, owner_key, task_id)

    def list_send_tasks(self, owner_key: str) -> list[dict[str, Any]]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                """
                select id, scrape_batch_id, source, channel, message_template,
                       status, created_at, updated_at, metadata_json, item_count,
                       pending_count, success_count, failed_count, skipped_count
                from send_tasks
                where owner_key = ?
                order by created_at desc
                """,
                (owner_key,),
            ).fetchall()
        return [row_to_send_task_summary(row) for row in rows]

    def get_send_task(self, owner_key: str, task_id: str) -> dict[str, Any] | None:
        with closing(self._connect()) as conn:
            task = self._get_send_task_summary(conn, owner_key, task_id)
            if task is None:
                return None
            rows = conn.execute(
                """
                select id, task_id, scrape_batch_id, customer_index, customer_json,
                       status, created_at, updated_at, result_json, last_error
                from send_task_items
                where owner_key = ? and task_id = ?
                order by customer_index asc
                """,
                (owner_key, task_id),
            ).fetchall()
        task["items"] = [row_to_send_task_item(row) for row in rows]
        return task

    def claim_next_send_task_item(
        self,
        owner_key: str,
        task_id: str,
        worker_id: str = "",
    ) -> dict[str, Any] | None:
        now = now_iso()
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            task = self._get_send_task_summary(conn, owner_key, task_id)
            if task is None:
                conn.rollback()
                return None

            item = conn.execute(
                """
                select id, task_id, scrape_batch_id, customer_index, customer_json,
                       status, created_at, updated_at, result_json, last_error
                from send_task_items
                where owner_key = ? and task_id = ? and status = 'pending'
                order by customer_index asc
                limit 1
                """,
                (owner_key, task_id),
            ).fetchone()
            if item is None:
                self._refresh_send_task_counts(conn, owner_key, task_id, now)
                task = self._get_send_task_summary(conn, owner_key, task_id)
                conn.commit()
                return {"item": None, "task": task}

            result = {"claimed_at": now}
            if worker_id:
                result["worker_id"] = worker_id
            conn.execute(
                """
                update send_task_items
                set status = 'in_progress', updated_at = ?, result_json = ?
                where owner_key = ? and id = ? and status = 'pending'
                """,
                (
                    now,
                    json.dumps(result, ensure_ascii=False),
                    owner_key,
                    item["id"],
                ),
            )
            self._refresh_send_task_counts(conn, owner_key, task_id, now)
            claimed = conn.execute(
                """
                select id, task_id, scrape_batch_id, customer_index, customer_json,
                       status, created_at, updated_at, result_json, last_error
                from send_task_items
                where owner_key = ? and id = ?
                """,
                (owner_key, item["id"]),
            ).fetchone()
            task = self._get_send_task_summary(conn, owner_key, task_id)
            conn.commit()
        if claimed is None or task is None:
            return None
        return {"item": row_to_send_task_item(claimed), "task": task}

    def update_send_task_item_result(
        self,
        owner_key: str,
        item_id: str,
        status: str,
        result: dict[str, Any] | None = None,
        last_error: str = "",
    ) -> dict[str, Any] | None:
        now = now_iso()
        with closing(self._connect()) as conn:
            existing = conn.execute(
                """
                select id, task_id
                from send_task_items
                where owner_key = ? and id = ?
                """,
                (owner_key, item_id),
            ).fetchone()
            if existing is None:
                return None
            conn.execute(
                """
                update send_task_items
                set status = ?, updated_at = ?, result_json = ?, last_error = ?
                where owner_key = ? and id = ?
                """,
                (
                    status,
                    now,
                    json.dumps(result or {}, ensure_ascii=False),
                    last_error,
                    owner_key,
                    item_id,
                ),
            )
            self._refresh_send_task_counts(conn, owner_key, existing["task_id"], now)
            item = conn.execute(
                """
                select id, task_id, scrape_batch_id, customer_index, customer_json,
                       status, created_at, updated_at, result_json, last_error
                from send_task_items
                where owner_key = ? and id = ?
                """,
                (owner_key, item_id),
            ).fetchone()
            task = self._get_send_task_summary(conn, owner_key, existing["task_id"])
            conn.commit()
        if item is None or task is None:
            return None
        return {"item": row_to_send_task_item(item), "task": task}

    def create_device_pairing_code(
        self,
        owner_key: str,
        device_name: str = "",
        ttl_seconds: int = PAIRING_CODE_TTL_SECONDS,
    ) -> dict[str, Any]:
        now = now_iso()
        expires_at = int(time.time()) + max(60, int(ttl_seconds))
        clean_device_name = str(device_name or "").strip()[:80]
        for _ in range(20):
            code = f"{secrets.randbelow(1000000):06d}"
            code_hash = hash_secret(code)
            try:
                with closing(self._connect()) as conn:
                    conn.execute(
                        """
                        insert into device_pairing_codes(
                          code_hash, owner_key, device_name, created_at,
                          expires_at, claimed_at, claimed_by
                        )
                        values (?, ?, ?, ?, ?, '', '')
                        """,
                        (code_hash, owner_key, clean_device_name, now, expires_at),
                    )
                    conn.commit()
                return {
                    "code": format_pairing_code(code),
                    "expires_at": expires_at,
                    "ttl_seconds": expires_at - int(time.time()),
                    "device_name": clean_device_name,
                }
            except sqlite3.IntegrityError:
                continue
        raise RuntimeError("failed to allocate pairing code")

    def exchange_device_pairing_code(
        self,
        code: str,
        device_name: str = "",
        device_id: str = "",
    ) -> dict[str, Any] | None:
        clean_code = normalize_pairing_code(code)
        if len(clean_code) != 6:
            return None
        now = now_iso()
        now_epoch = int(time.time())
        token = DEVICE_TOKEN_PREFIX + secrets.token_urlsafe(32)
        token_hash = hash_secret(token)
        device_public_id = new_device_public_id()
        clean_device_name = str(device_name or "").strip()[:80]
        clean_device_id = str(device_id or "").strip()[:120]
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                select code_hash, owner_key, device_name, expires_at, claimed_at
                from device_pairing_codes
                where code_hash = ?
                """,
                (hash_secret(clean_code),),
            ).fetchone()
            if row is None or row["claimed_at"] or int(row["expires_at"]) < now_epoch:
                conn.rollback()
                return None
            owner_key = row["owner_key"]
            paired_name = clean_device_name or row["device_name"] or "Android sender"
            conn.execute(
                """
                update device_pairing_codes
                set claimed_at = ?, claimed_by = ?
                where code_hash = ?
                """,
                (now, clean_device_id or paired_name, row["code_hash"]),
            )
            conn.execute(
                """
                insert into device_tokens(
                  token_hash, id, owner_key, device_name, device_id,
                  created_at, last_seen_at, revoked_at
                )
                values (?, ?, ?, ?, ?, ?, ?, '')
                """,
                (token_hash, device_public_id, owner_key, paired_name, clean_device_id, now, now),
            )
            conn.commit()
        return {
            "device_token": token,
            "owner_key": owner_key,
            "device_name": paired_name,
        }

    def list_device_tokens(self, owner_key: str) -> list[dict[str, Any]]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                """
                select token_hash, id, device_name, device_id, created_at, last_seen_at, revoked_at
                from device_tokens
                where owner_key = ?
                order by created_at desc
                """,
                (owner_key,),
            ).fetchall()
            devices: list[dict[str, Any]] = []
            for row in rows:
                public_id = row["id"] or new_device_public_id()
                if not row["id"]:
                    conn.execute(
                        "update device_tokens set id = ? where token_hash = ?",
                        (public_id, row["token_hash"]),
                    )
                devices.append({
                    "id": public_id,
                    "device_name": row["device_name"] or "",
                    "device_id": row["device_id"] or "",
                    "created_at": row["created_at"] or "",
                    "last_seen_at": row["last_seen_at"] or "",
                    "revoked_at": row["revoked_at"] or "",
                })
            conn.commit()
        return devices

    def revoke_device_token(self, owner_key: str, device_public_id: str) -> dict[str, Any] | None:
        clean_id = str(device_public_id or "").strip()
        if not clean_id:
            return None
        now = now_iso()
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                select id, device_name, device_id, created_at, last_seen_at, revoked_at
                from device_tokens
                where owner_key = ? and id = ?
                """,
                (owner_key, clean_id),
            ).fetchone()
            if row is None:
                conn.rollback()
                return None
            revoked_at = row["revoked_at"] or now
            conn.execute(
                "update device_tokens set revoked_at = ? where owner_key = ? and id = ?",
                (revoked_at, owner_key, clean_id),
            )
            conn.commit()
        return {
            "id": row["id"],
            "device_name": row["device_name"] or "",
            "device_id": row["device_id"] or "",
            "created_at": row["created_at"] or "",
            "last_seen_at": row["last_seen_at"] or "",
            "revoked_at": revoked_at,
        }

    def owner_key_for_device_token(self, token: str) -> dict[str, Any] | None:
        if not token.startswith(DEVICE_TOKEN_PREFIX):
            return None
        token_hash = hash_secret(token)
        now = now_iso()
        with closing(self._connect()) as conn:
            row = conn.execute(
                """
                select owner_key, device_name, device_id
                from device_tokens
                where token_hash = ? and (revoked_at is null or revoked_at = '')
                """,
                (token_hash,),
            ).fetchone()
            if row is None:
                return None
            conn.execute(
                "update device_tokens set last_seen_at = ? where token_hash = ?",
                (now, token_hash),
            )
            conn.commit()
        return {
            "owner_key": row["owner_key"],
            "device_name": row["device_name"],
            "device_id": row["device_id"],
        }

    def create_mobile_asset(
        self,
        owner_key: str,
        filename: str,
        mime: str,
        content: bytes,
    ) -> dict[str, Any]:
        asset_id = uuid.uuid4().hex
        ext = Path(filename).suffix.lower()
        stored_name = f"{asset_id}{ext}"
        rel_path = f"cloud/mobile-assets/{stored_name}"
        asset_path = self.data_root / rel_path
        digest = hashlib.sha256(content).hexdigest()
        now = now_iso()
        asset_path.write_bytes(content)
        asset = {
            "id": asset_id,
            "owner_key": owner_key,
            "filename": filename,
            "mime": mime,
            "sha256": digest,
            "size": len(content),
            "created_at": now,
            "rel_path": rel_path,
        }
        with closing(self._connect()) as conn:
            conn.execute(
                """
                insert into mobile_assets(
                  id, owner_key, filename, mime, sha256, size, created_at, rel_path
                )
                values (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    asset["id"],
                    owner_key,
                    filename,
                    mime,
                    digest,
                    len(content),
                    now,
                    rel_path,
                ),
            )
            conn.commit()
        return asset

    def get_mobile_asset(self, owner_key: str, asset_id: str) -> dict[str, Any] | None:
        with closing(self._connect()) as conn:
            row = conn.execute(
                """
                select id, owner_key, filename, mime, sha256, size, created_at, rel_path
                from mobile_assets
                where owner_key = ? and id = ?
                """,
                (owner_key, asset_id),
            ).fetchone()
        if row is None:
            return None
        return row_to_mobile_asset(row)

    def _get_send_task_summary(
        self,
        conn: sqlite3.Connection,
        owner_key: str,
        task_id: str,
    ) -> dict[str, Any] | None:
        row = conn.execute(
            """
            select id, scrape_batch_id, source, channel, message_template,
                   status, created_at, updated_at, metadata_json, item_count,
                   pending_count, success_count, failed_count, skipped_count
            from send_tasks
            where owner_key = ? and id = ?
            """,
            (owner_key, task_id),
        ).fetchone()
        if row is None:
            return None
        return row_to_send_task_summary(row)

    def _refresh_send_task_counts(
        self,
        conn: sqlite3.Connection,
        owner_key: str,
        task_id: str,
        updated_at: str,
    ) -> None:
        rows = conn.execute(
            """
            select status, count(*) as count
            from send_task_items
            where owner_key = ? and task_id = ?
            group by status
            """,
            (owner_key, task_id),
        ).fetchall()
        counts = {row["status"]: int(row["count"]) for row in rows}
        item_count = sum(counts.values())
        success_count = counts.get("success", 0)
        failed_count = counts.get("failed", 0)
        skipped_count = counts.get("skipped", 0)
        in_progress_count = counts.get("in_progress", 0)
        pending_count = counts.get("pending", 0)
        if item_count == 0:
            task_status = "empty"
        elif pending_count > 0 and (success_count or failed_count or skipped_count or in_progress_count):
            task_status = "in_progress"
        elif in_progress_count > 0:
            task_status = "in_progress"
        elif pending_count > 0:
            task_status = "pending"
        elif failed_count > 0:
            task_status = "done_with_errors"
        else:
            task_status = "done"
        conn.execute(
            """
            update send_tasks
            set status = ?, updated_at = ?, item_count = ?, pending_count = ?,
                success_count = ?, failed_count = ?, skipped_count = ?
            where owner_key = ? and id = ?
            """,
            (
                task_status,
                updated_at,
                item_count,
                pending_count,
                success_count,
                failed_count,
                skipped_count,
                owner_key,
                task_id,
            ),
        )


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
        if parsed.path == "/api/devices":
            self.handle_list_devices()
            return
        if parsed.path == "/api/scrape-batches":
            self.handle_list_scrape_batches()
            return
        if parsed.path.startswith("/api/scrape-batches/"):
            self.handle_get_scrape_batch(parsed.path.removeprefix("/api/scrape-batches/"))
            return
        if parsed.path == "/api/send-tasks":
            self.handle_list_send_tasks()
            return
        if parsed.path.startswith("/api/send-tasks/"):
            self.handle_get_send_task(parsed.path.removeprefix("/api/send-tasks/"))
            return
        if parsed.path.startswith("/api/mobile-assets/"):
            self.handle_get_mobile_asset(parsed.path.removeprefix("/api/mobile-assets/"))
            return
        if parsed.path.startswith("/api/"):
            self.send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            return
        self.serve_static(parsed.path)

    def do_POST(self) -> None:  # noqa: N802 - stdlib method name
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/device-pairings":
            self.handle_create_device_pairing()
            return
        if parsed.path == "/api/device-pairings/exchange":
            self.handle_exchange_device_pairing()
            return
        if parsed.path.startswith("/api/devices/") and parsed.path.endswith("/revoke"):
            raw_device_id = parsed.path.removeprefix("/api/devices/").removesuffix("/revoke")
            self.handle_revoke_device(raw_device_id)
            return
        if parsed.path == "/api/scrape-batches":
            self.handle_create_scrape_batch()
            return
        if parsed.path == "/api/send-tasks":
            self.handle_create_send_task()
            return
        if parsed.path == "/api/mobile-assets":
            self.handle_create_mobile_asset()
            return
        if parsed.path.startswith("/api/send-tasks/") and parsed.path.endswith("/claim-next"):
            raw_task_id = parsed.path.removeprefix("/api/send-tasks/").removesuffix("/claim-next")
            self.handle_claim_next_send_task_item(raw_task_id)
            return
        if parsed.path.startswith("/api/send-task-items/") and parsed.path.endswith("/result"):
            raw_item_id = parsed.path.removeprefix("/api/send-task-items/").removesuffix("/result")
            self.handle_update_send_task_item_result(raw_item_id)
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
        metadata = body["metadata"] if "metadata" in body else {}
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

    def handle_create_send_task(self) -> None:
        ctx = self.require_user()
        if ctx is None:
            return
        body = self.read_json_body()
        if body is None:
            return
        scrape_batch_id = str(body.get("scrape_batch_id") or "").strip()
        if not scrape_batch_id:
            self.send_json({"error": "scrape_batch_id is required"}, HTTPStatus.BAD_REQUEST)
            return
        channel = str(body.get("channel") or "wecom").strip() or "wecom"
        message_template = str(body.get("message_template") or "").strip()
        metadata = body["metadata"] if "metadata" in body else {}
        if not isinstance(metadata, dict):
            self.send_json({"error": "metadata must be an object"}, HTTPStatus.BAD_REQUEST)
            return
        self.server.storage.touch_user(ctx["owner_key"], ctx["user"])  # type: ignore[attr-defined]
        task = self.server.storage.create_send_task_from_batch(  # type: ignore[attr-defined]
            ctx["owner_key"],
            scrape_batch_id,
            channel,
            message_template,
            metadata,
        )
        if task is None:
            self.send_json({"error": "scrape batch not found"}, HTTPStatus.NOT_FOUND)
            return
        self.send_json({"ok": True, "task": task}, HTTPStatus.CREATED)

    def handle_list_send_tasks(self) -> None:
        ctx = self.require_task_consumer()
        if ctx is None:
            return
        self.touch_user_context(ctx)
        tasks = self.server.storage.list_send_tasks(ctx["owner_key"])  # type: ignore[attr-defined]
        self.send_json({"ok": True, "tasks": tasks})

    def handle_get_send_task(self, raw_task_id: str) -> None:
        ctx = self.require_task_consumer()
        if ctx is None:
            return
        task_id = urllib.parse.unquote(raw_task_id).strip("/")
        if not task_id:
            self.send_json({"error": "missing task id"}, HTTPStatus.BAD_REQUEST)
            return
        task = self.server.storage.get_send_task(ctx["owner_key"], task_id)  # type: ignore[attr-defined]
        if task is None:
            self.send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            return
        self.send_json({"ok": True, "task": task})

    def handle_claim_next_send_task_item(self, raw_task_id: str) -> None:
        ctx = self.require_task_consumer()
        if ctx is None:
            return
        task_id = urllib.parse.unquote(raw_task_id).strip("/")
        if not task_id:
            self.send_json({"error": "missing task id"}, HTTPStatus.BAD_REQUEST)
            return
        body = self.read_optional_json_body()
        if body is None:
            return
        worker_id = str(body.get("worker_id") or "").strip()
        self.touch_user_context(ctx)
        claimed = self.server.storage.claim_next_send_task_item(  # type: ignore[attr-defined]
            ctx["owner_key"],
            task_id,
            worker_id,
        )
        if claimed is None:
            self.send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            return
        self.send_json({"ok": True, **claimed})

    def handle_update_send_task_item_result(self, raw_item_id: str) -> None:
        ctx = self.require_task_consumer()
        if ctx is None:
            return
        item_id = urllib.parse.unquote(raw_item_id).strip("/")
        if not item_id:
            self.send_json({"error": "missing item id"}, HTTPStatus.BAD_REQUEST)
            return
        body = self.read_json_body()
        if body is None:
            return
        status = str(body.get("status") or "").strip()
        if status not in {"pending", "success", "failed", "skipped"}:
            self.send_json(
                {"error": "status must be one of pending, success, failed, skipped"},
                HTTPStatus.BAD_REQUEST,
            )
            return
        result = body.get("result") or {}
        if not isinstance(result, dict):
            self.send_json({"error": "result must be an object"}, HTTPStatus.BAD_REQUEST)
            return
        last_error = str(body.get("last_error") or "").strip()
        self.touch_user_context(ctx)
        updated = self.server.storage.update_send_task_item_result(  # type: ignore[attr-defined]
            ctx["owner_key"],
            item_id,
            status,
            result,
            last_error,
        )
        if updated is None:
            self.send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            return
        self.send_json({"ok": True, **updated})

    def handle_create_mobile_asset(self) -> None:
        ctx = self.require_user()
        if ctx is None:
            return
        body = self.read_json_body()
        if body is None:
            return
        filename = sanitize_mobile_asset_filename(str(body.get("filename") or ""))
        if not filename:
            self.send_json({"error": "filename is required"}, HTTPStatus.BAD_REQUEST)
            return
        ext = Path(filename).suffix.lower()
        mime = MOBILE_ASSET_MIME_BY_EXT.get(ext)
        if not mime:
            self.send_json({"error": "unsupported image type"}, HTTPStatus.BAD_REQUEST)
            return
        content_base64 = str(body.get("content_base64") or "")
        if not content_base64:
            self.send_json({"error": "content_base64 is required"}, HTTPStatus.BAD_REQUEST)
            return
        try:
            content = base64.b64decode(content_base64, validate=True)
        except Exception:
            self.send_json({"error": "invalid content_base64"}, HTTPStatus.BAD_REQUEST)
            return
        if not content:
            self.send_json({"error": "image content is empty"}, HTTPStatus.BAD_REQUEST)
            return
        if len(content) > MOBILE_ASSET_MAX_BYTES:
            self.send_json({"error": "image content too large"}, HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
            return
        self.server.storage.touch_user(ctx["owner_key"], ctx["user"])  # type: ignore[attr-defined]
        asset = self.server.storage.create_mobile_asset(  # type: ignore[attr-defined]
            ctx["owner_key"],
            filename,
            mime,
            content,
        )
        self.send_json(
            {"ok": True, "asset": public_mobile_asset(asset, self.public_api_base_url())},
            HTTPStatus.CREATED,
        )

    def handle_get_mobile_asset(self, raw_asset_id: str) -> None:
        ctx = self.require_task_consumer()
        if ctx is None:
            return
        asset_id = urllib.parse.unquote(raw_asset_id).strip("/")
        if not asset_id:
            self.send_json({"error": "missing asset id"}, HTTPStatus.BAD_REQUEST)
            return
        asset = self.server.storage.get_mobile_asset(ctx["owner_key"], asset_id)  # type: ignore[attr-defined]
        if asset is None:
            self.send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            return
        asset_path = (self.server.storage.data_root / asset["rel_path"]).resolve()  # type: ignore[attr-defined]
        try:
            asset_path.relative_to(self.server.storage.data_root.resolve())  # type: ignore[attr-defined]
            content = asset_path.read_bytes()
        except (OSError, ValueError):
            self.send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            return

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", asset["mime"])
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "private, max-age=300")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(content)

    def handle_create_device_pairing(self) -> None:
        ctx = self.require_user()
        if ctx is None:
            return
        body = self.read_optional_json_body()
        if body is None:
            return
        device_name = str(body.get("device_name") or "").strip()
        self.server.storage.touch_user(ctx["owner_key"], ctx["user"])  # type: ignore[attr-defined]
        pairing = self.server.storage.create_device_pairing_code(  # type: ignore[attr-defined]
            ctx["owner_key"],
            device_name,
        )
        self.send_json({"ok": True, "pairing": pairing}, HTTPStatus.CREATED)

    def handle_exchange_device_pairing(self) -> None:
        body = self.read_json_body()
        if body is None:
            return
        code = str(body.get("code") or "").strip()
        device_name = str(body.get("device_name") or "").strip()
        device_id = str(body.get("device_id") or "").strip()
        if not normalize_pairing_code(code):
            self.send_json({"error": "code is required"}, HTTPStatus.BAD_REQUEST)
            return
        exchanged = self.server.storage.exchange_device_pairing_code(  # type: ignore[attr-defined]
            code,
            device_name,
            device_id,
        )
        if exchanged is None:
            self.send_json({"error": "invalid or expired pairing code"}, HTTPStatus.UNAUTHORIZED)
            return
        self.send_json({
            "ok": True,
            "device_token": exchanged["device_token"],
            "device_name": exchanged["device_name"],
        })

    def handle_list_devices(self) -> None:
        ctx = self.require_user()
        if ctx is None:
            return
        devices = self.server.storage.list_device_tokens(ctx["owner_key"])  # type: ignore[attr-defined]
        self.send_json({"ok": True, "devices": devices})

    def handle_revoke_device(self, raw_device_id: str) -> None:
        ctx = self.require_user()
        if ctx is None:
            return
        device_id = urllib.parse.unquote(str(raw_device_id or "").strip())
        device = self.server.storage.revoke_device_token(ctx["owner_key"], device_id)  # type: ignore[attr-defined]
        if device is None:
            self.send_json({"error": "device not found"}, HTTPStatus.NOT_FOUND)
            return
        self.send_json({"ok": True, "device": device})

    def require_user(self) -> dict[str, Any] | None:
        token = extract_bearer_token(self.headers.get("Authorization"))
        if not token:
            self.send_json({"error": "missing bearer token"}, HTTPStatus.UNAUTHORIZED)
            return None
        if token.startswith(DEVICE_TOKEN_PREFIX):
            self.send_json({"error": "user token required"}, HTTPStatus.UNAUTHORIZED)
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
        return {"auth_type": "user", "user": user, "owner_key": owner_key}

    def require_task_consumer(self) -> dict[str, Any] | None:
        token = extract_bearer_token(self.headers.get("Authorization"))
        if not token:
            self.send_json({"error": "missing bearer token"}, HTTPStatus.UNAUTHORIZED)
            return None
        if token.startswith(DEVICE_TOKEN_PREFIX):
            device = self.server.storage.owner_key_for_device_token(token)  # type: ignore[attr-defined]
            if device is None:
                self.send_json({"error": "not authenticated"}, HTTPStatus.UNAUTHORIZED)
                return None
            return {"auth_type": "device", **device}
        return self.require_user()

    def touch_user_context(self, ctx: dict[str, Any]) -> None:
        user = ctx.get("user")
        if isinstance(user, dict):
            self.server.storage.touch_user(ctx["owner_key"], user)  # type: ignore[attr-defined]

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

    def read_optional_json_body(self) -> dict[str, Any] | None:
        length_raw = self.headers.get("Content-Length") or "0"
        try:
            length = int(length_raw)
        except ValueError:
            self.send_json({"error": "invalid content length"}, HTTPStatus.BAD_REQUEST)
            return None
        if length <= 0:
            return {}
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

    def public_api_base_url(self) -> str:
        proto = (self.headers.get("X-Forwarded-Proto") or "http").split(",")[0].strip() or "http"
        host = self.headers.get("Host") or f"{self.server.server_name}:{self.server.server_port}"
        return f"{proto}://{host}"

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


def sanitize_mobile_asset_filename(value: str) -> str:
    filename = Path(value.strip()).name
    if not filename or filename in {".", ".."}:
        return ""
    stem = Path(filename).stem.strip()
    ext = Path(filename).suffix.lower()
    if not stem or ext not in MOBILE_ASSET_MIME_BY_EXT:
        return ""
    safe_chars = []
    for ch in stem[:80]:
        safe_chars.append(ch if ch.isalnum() or ch in {"-", "_", "."} else "_")
    safe_stem = "".join(safe_chars).strip("._")
    if not safe_stem:
        safe_stem = "image"
    return f"{safe_stem}{ext}"


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


def row_to_send_task_summary(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "scrape_batch_id": row["scrape_batch_id"],
        "source": row["source"],
        "channel": row["channel"],
        "message_template": row["message_template"],
        "status": row["status"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "metadata": json.loads(row["metadata_json"] or "{}"),
        "item_count": row["item_count"],
        "pending_count": row["pending_count"],
        "success_count": row["success_count"],
        "failed_count": row["failed_count"],
        "skipped_count": row["skipped_count"],
    }


def row_to_send_task_item(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "task_id": row["task_id"],
        "scrape_batch_id": row["scrape_batch_id"],
        "customer_index": row["customer_index"],
        "customer": json.loads(row["customer_json"] or "{}"),
        "status": row["status"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "result": json.loads(row["result_json"] or "{}"),
        "last_error": row["last_error"],
    }


def row_to_mobile_asset(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "owner_key": row["owner_key"],
        "filename": row["filename"],
        "mime": row["mime"],
        "sha256": row["sha256"],
        "size": row["size"],
        "created_at": row["created_at"],
        "rel_path": row["rel_path"],
    }


def public_mobile_asset(asset: dict[str, Any], base_url: str) -> dict[str, Any]:
    return {
        "id": asset["id"],
        "image_url": f"{base_url}/api/mobile-assets/{asset['id']}",
        "image_name": asset["filename"],
        "image_mime": asset["mime"],
        "image_sha256": asset["sha256"],
        "size": asset["size"],
        "created_at": asset["created_at"],
    }


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
