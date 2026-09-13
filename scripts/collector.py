#!/usr/bin/env python3
"""Minis relay collector daemon — runs on the Mac mini.

Responsibilities:
- Poll Feishu for relay messages, dedupe by message_id into SQLite.
- Expose a small bearer-token HTTP API so the Windows Hermes Desktop plugin
  can fetch its pending events and confirm delivery.
- After a push event is confirmed delivered, send [MINIS_PUSH_ACK] to Feishu
  so the phone stops offering it again.

State survives restarts; a reply is delivered at most once, even if Windows
is offline for days.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))

from feishu_relay import encode_push_ack, make_client
from receiver import RelayStore

VERSION = "1.0.0"
DEFAULT_DB = Path.home() / "minis-receiver" / "receiver.db"
DEFAULT_PORT = 8786
POLL_INTERVAL = 5.0
BACKOFF_CAP = 60.0


class Collector:
    """Poll loop + delivery confirmation, decoupled from HTTP."""

    def __init__(self, client, store: RelayStore):
        self.client = client
        self.store = store

    def poll_once(self) -> int:
        items = self.client.list_messages(page_size=50)
        return self.store.ingest(items)

    def pending(self, node: str) -> list[dict]:
        return self.store.pending(node)

    def deliver(self, event_id: str) -> bool:
        """Mark delivered; ACK pushes back to Feishu. Requires a prior claim.
        Returns False if already delivered or never claimed."""
        event = self.store.get_event(event_id)
        if event is None:
            return False
        if not self.store.mark_delivered(event_id):
            return False
        if event["kind"] == "push":
            try:
                self.client.send_text(encode_push_ack(event["request_id"]))
            except Exception as exc:  # ACK failure must not un-deliver
                print(f"[collector] feishu ACK failed for {event_id}: {exc}", flush=True)
        return True


def make_handler(collector: Collector, token: str):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):  # keep stdout for journal lines
            print(f"[http] {self.address_string()} {fmt % args}", flush=True)

        def _json(self, status: int, payload: dict):
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _authorized(self) -> bool:
            auth = self.headers.get("Authorization", "")
            return bool(token) and auth == f"Bearer {token}"

        def do_GET(self):
            parsed = urlparse(self.path)
            if parsed.path == "/health":
                self._json(200, {"ok": True, "version": VERSION})
                return
            if parsed.path == "/api/pending":
                if not self._authorized():
                    self._json(401, {"error": "unauthorized"})
                    return
                node = (parse_qs(parsed.query).get("node") or [""])[0]
                self._json(200, {"events": collector.pending(node)})
                return
            self._json(404, {"error": "not found"})

        def do_POST(self):
            parsed = urlparse(self.path)
            if parsed.path not in ("/api/deliver", "/api/claim", "/api/release"):
                self._json(404, {"error": "not found"})
                return
            if not self._authorized():
                self._json(401, {"error": "unauthorized"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
            except (ValueError, json.JSONDecodeError):
                self._json(400, {"error": "bad json"})
                return
            event_id = str(payload.get("event_id", ""))
            if not event_id:
                self._json(400, {"error": "event_id required"})
                return
            if parsed.path == "/api/claim":
                if collector.store.claim(event_id):
                    self._json(200, {"ok": True, "event_id": event_id})
                else:
                    self._json(409, {"ok": False, "error": "already claimed or not pending"})
                return
            if parsed.path == "/api/release":
                if collector.store.release(event_id):
                    self._json(200, {"ok": True, "event_id": event_id})
                else:
                    self._json(409, {"ok": False, "error": "not claimed"})
                return
            if collector.deliver(event_id):
                self._json(200, {"ok": True, "event_id": event_id})
            else:
                self._json(409, {"ok": False, "error": "already delivered, unknown, or not claimed"})

    return Handler


def serve_in_thread(collector: Collector, token: str, port: int = DEFAULT_PORT,
                    host: str = "127.0.0.1"):
    """Start the API on a background thread. Returns (server, bound_port).

    Binds 127.0.0.1 by default (tests); production passes 0.0.0.0 so the
    Windows desktop can reach it over Tailscale.
    """
    server = ThreadingHTTPServer((host, port), make_handler(collector, token))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, server.server_address[1]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Minis relay collector daemon")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--bind", default="0.0.0.0")
    parser.add_argument("--interval", type=float, default=POLL_INTERVAL)
    parser.add_argument("--once", action="store_true", help="poll once and exit")
    args = parser.parse_args(argv)

    token = os.environ.get("MINIS_COLLECTOR_TOKEN", "").strip()
    if not token:
        token_path = Path.home() / "minis-receiver" / "token"
        if token_path.exists():
            token = token_path.read_text(encoding="utf-8").strip()
        else:
            token = secrets.token_urlsafe(24)
            token_path.parent.mkdir(parents=True, exist_ok=True)
            token_path.write_text(token + "\n", encoding="utf-8")
            os.chmod(token_path, 0o600)
            print(f"[collector] generated new API token at {token_path}", flush=True)

    store = RelayStore(args.db)
    collector = Collector(make_client(), store)
    server, bound_port = serve_in_thread(collector, token, port=args.port, host=args.bind)
    print(f"[collector] v{VERSION} listening on {args.bind}:{bound_port}, db={args.db}", flush=True)

    backoff = args.interval
    try:
        while True:
            try:
                new = collector.poll_once()
                backoff = args.interval
                if new:
                    print(f"[collector] ingested {new} new message(s)", flush=True)
            except Exception as exc:
                print(f"[collector] poll error: {exc}; retry in {backoff:.0f}s", flush=True)
                backoff = min(backoff * 2, BACKOFF_CAP)
            if args.once:
                break
            time.sleep(args.interval if backoff == args.interval else backoff)
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
