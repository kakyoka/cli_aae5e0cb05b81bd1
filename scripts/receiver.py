#!/usr/bin/env python3
"""Durable SQLite store for the Minis relay collector.

The Mac mini collector ingests Feishu messages into this store. Replies and
pushes become delivery *events*; a reply inherits the origin route (node /
profile / session_id) from the request that asked the question, so the
Windows deliverer can return the answer to the exact Hermes session that
sent it — even across restarts and offline windows.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Iterable

from feishu_relay import _message_envelope

_SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    message_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    request_id TEXT NOT NULL,
    body TEXT NOT NULL,
    route_json TEXT NOT NULL DEFAULT '{}',
    create_time INTEGER NOT NULL DEFAULT 0,
    ingested_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    event_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    request_id TEXT NOT NULL,
    body TEXT NOT NULL,
    node TEXT NOT NULL DEFAULT '',
    profile TEXT NOT NULL DEFAULT '',
    session_id TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    created_at REAL NOT NULL,
    claimed_at REAL,
    delivered_at REAL
);
CREATE INDEX IF NOT EXISTS idx_events_pending ON events(status, node, created_at);
CREATE INDEX IF NOT EXISTS idx_messages_request ON messages(kind, request_id);
"""


class RelayStore:
    CLAIM_TTL = 300.0  # seconds; an abandoned claim requeues automatically

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._con = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._lock = threading.Lock()
        self._con.row_factory = sqlite3.Row
        self._con.execute("PRAGMA journal_mode=WAL")
        self._con.executescript(_SCHEMA)
        self._con.commit()

    def close(self) -> None:
        self._con.close()

    def ingest(self, items: Iterable[dict]) -> int:
        """Insert new relay messages; create events for replies and pushes.

        Returns the number of NEW Feishu messages accepted (dedupe by
        message_id). Idempotent: re-ingesting the same list is a no-op.
        """
        new_count = 0
        ordered = sorted(items, key=lambda x: int(x.get("create_time", 0)))
        with self._lock:
            return self._ingest_locked(ordered, new_count)

    def _ingest_locked(self, ordered, new_count: int) -> int:
        for item in ordered:
            env = _message_envelope(item)
            if env is None:
                continue
            message_id = str(item.get("message_id", ""))
            if not message_id:
                continue
            cur = self._con.execute(
                "INSERT OR IGNORE INTO messages"
                " (message_id, kind, request_id, body, route_json, create_time, ingested_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    message_id,
                    env.kind,
                    env.request_id,
                    env.body,
                    json.dumps(env.route, ensure_ascii=False),
                    int(item.get("create_time", 0)),
                    time.time(),
                ),
            )
            if cur.rowcount == 0:
                continue  # already seen this Feishu message
            new_count += 1
            if env.kind == "push_ack":
                # An ACK (in the same batch or arriving later) retires the
                # push: nobody needs to deliver what was already handled.
                self._con.execute(
                    "UPDATE events SET status = 'delivered',"
                    " delivered_at = COALESCE(delivered_at, ?)"
                    " WHERE event_id = ? AND status = 'pending'",
                    (time.time(), f"push:{env.request_id}"),
                )
                continue
            if env.kind == "reply":
                route = self._route_for_request(env.request_id)
                self._con.execute(
                    "INSERT OR IGNORE INTO events"
                    " (event_id, kind, request_id, body, node, profile, session_id, status, created_at)"
                    " VALUES (?, 'reply', ?, ?, ?, ?, ?, 'pending', ?)",
                    (
                        f"reply:{env.request_id}",
                        env.request_id,
                        env.body,
                        route.get("node", ""),
                        route.get("profile", ""),
                        route.get("session_id", ""),
                        time.time(),
                    ),
                )
            elif env.kind == "push":
                if self._push_was_acked(env.request_id):
                    continue
                self._con.execute(
                    "INSERT OR IGNORE INTO events"
                    " (event_id, kind, request_id, body, node, profile, session_id, status, created_at)"
                    " VALUES (?, 'push', ?, ?, ?, ?, ?, 'pending', ?)",
                    (
                        f"push:{env.request_id}",
                        env.request_id,
                        env.body,
                        env.route.get("node", ""),
                        env.route.get("profile", ""),
                        env.route.get("session_id", ""),
                        time.time(),
                    ),
                )
        self._con.commit()
        return new_count

    def _push_was_acked(self, push_id: str) -> bool:
        row = self._con.execute(
            "SELECT 1 FROM messages WHERE kind = 'push_ack' AND request_id = ? LIMIT 1",
            (push_id,),
        ).fetchone()
        return row is not None

    def _route_for_request(self, request_id: str) -> dict[str, str]:
        row = self._con.execute(
            "SELECT route_json FROM messages WHERE kind = 'request' AND request_id = ?"
            " ORDER BY create_time DESC LIMIT 1",
            (request_id,),
        ).fetchone()
        if row is None:
            return {}
        try:
            route = json.loads(row["route_json"])
        except (ValueError, TypeError):
            return {}
        return route if isinstance(route, dict) else {}

    def pending(self, node: str) -> list[dict]:
        """Undelivered events for one node.

        Replies match strictly by their origin node. Pushes carry no route,
        so any node may claim them; first successful delivery wins.
        Events with an expired claim lease requeue automatically.
        """
        with self._lock:
            cutoff = time.time() - self.CLAIM_TTL
            self._con.execute(
                "UPDATE events SET status = 'pending', claimed_at = NULL"
                " WHERE status = 'claimed' AND claimed_at < ?",
                (cutoff,),
            )
            self._con.commit()
            rows = self._con.execute(
                "SELECT event_id, kind, request_id, body, node, profile, session_id"
                " FROM events WHERE status = 'pending' AND (node = ? OR kind = 'push')"
                " ORDER BY created_at",
                (node,),
            ).fetchall()
        return [
            {
                "event_id": r["event_id"],
                "kind": r["kind"],
                "request_id": r["request_id"],
                "body": r["body"],
                "node": r["node"],
                "profile": r["profile"],
                "session_id": r["session_id"],
            }
            for r in rows
        ]

    def claim(self, event_id: str) -> bool:
        """Atomic pending → claimed lease. False = someone else holds it."""
        with self._lock:
            cutoff = time.time() - self.CLAIM_TTL
            self._con.execute(
                "UPDATE events SET status = 'pending', claimed_at = NULL"
                " WHERE status = 'claimed' AND claimed_at < ?",
                (cutoff,),
            )
            cur = self._con.execute(
                "UPDATE events SET status = 'claimed', claimed_at = ?"
                " WHERE event_id = ? AND status = 'pending'",
                (time.time(), event_id),
            )
            self._con.commit()
            return cur.rowcount == 1

    def release(self, event_id: str) -> bool:
        """claimed → pending after a failed submit. False = not claimed."""
        with self._lock:
            cur = self._con.execute(
                "UPDATE events SET status = 'pending', claimed_at = NULL"
                " WHERE event_id = ? AND status = 'claimed'",
                (event_id,),
            )
            self._con.commit()
            return cur.rowcount == 1

    def mark_delivered(self, event_id: str) -> bool:
        """claimed → delivered. False = already done or never claimed."""
        with self._lock:
            cur = self._con.execute(
                "UPDATE events SET status = 'delivered', delivered_at = ?"
                " WHERE event_id = ? AND status = 'claimed'",
                (time.time(), event_id),
            )
            self._con.commit()
            return cur.rowcount == 1

    def get_event(self, event_id: str) -> dict | None:
        with self._lock:
            row = self._con.execute(
                "SELECT event_id, kind, request_id, body, node, profile, session_id, status"
                " FROM events WHERE event_id = ?",
                (event_id,),
            ).fetchone()
            return dict(row) if row else None
