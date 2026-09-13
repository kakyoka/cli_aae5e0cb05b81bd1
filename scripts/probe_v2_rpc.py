#!/usr/bin/env python3
"""Probe the CN Desktop v2 RPC door: SSE client_id + async JSON-RPC.

Verifies the exact chain the deliverer will use:
  GET /api/v2/events?token= → client_id
  POST /api/v2/rpc (session.resume, lazy) → runtime id via SSE
  POST /api/v2/rpc (session.status) → busy state via SSE
No prompt.submit here — this probe is read-only.
"""

import json
import re
import threading
import time
import urllib.request

BASE = "http://127.0.0.1:9120"
STORED_SESSION = "20260910_143138_9debff"


def get_token() -> str:
    html = urllib.request.urlopen(BASE + "/", timeout=8).read().decode("utf-8", "ignore")
    return re.search(r'__HERMES_SESSION_TOKEN__\s*=\s*["\']([^"\']+)', html).group(1)


class V2Client:
    def __init__(self, token: str):
        self.token = token
        self.client_id = ""
        self.responses = {}
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._listen, daemon=True)
        self._thread.start()
        for _ in range(100):
            if self.client_id:
                break
            time.sleep(0.05)
        if not self.client_id:
            raise RuntimeError("no client_id from /api/v2/events")

    def _listen(self):
        resp = urllib.request.urlopen(BASE + "/api/v2/events?token=" + self.token, timeout=300)
        pending_data = ""
        while True:
            line = resp.readline()
            if not line:
                break
            s = line.decode("utf-8", "ignore").rstrip("\n")
            if s.startswith("data:"):
                pending_data = s[5:].strip()
                try:
                    payload = json.loads(pending_data)
                except ValueError:
                    continue
                if "client_id" in payload:
                    self.client_id = payload["client_id"]
                elif payload.get("jsonrpc") == "2.0" and "id" in payload:
                    with self._lock:
                        self.responses[payload["id"]] = payload

    def rpc(self, method: str, params: dict, timeout: float = 30) -> dict:
        rid = f"probe-{time.time_ns()}"
        req = urllib.request.Request(
            BASE + "/api/v2/rpc",
            data=json.dumps({"jsonrpc": "2.0", "id": rid, "method": method, "params": params}).encode(),
            headers={
                "Content-Type": "application/json",
                "X-Hermes-Session-Token": self.token,
                "X-Hermes-Client-Id": self.client_id,
            },
            method="POST",
        )
        ack = json.loads(urllib.request.urlopen(req, timeout=20).read().decode())
        if "error" in ack:
            return ack  # synchronous error
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self._lock:
                if rid in self.responses:
                    return self.responses.pop(rid)
            time.sleep(0.1)
        raise TimeoutError(f"rpc {method} timed out waiting for SSE result")


def main():
    client = V2Client(get_token())
    print("client_id:", client.client_id)

    r = client.rpc("session.list", {"limit": 3})
    sessions = (r.get("result") or {}).get("sessions") or []
    print("session.list:", len(sessions), "sessions")
    for s in sessions[:3]:
        print(" -", s.get("id"), "|", (s.get("title") or "")[:40], "| active:", s.get("is_active"))

    r = client.rpc("session.resume", {"session_id": STORED_SESSION, "lazy": True}, timeout=60)
    res = r.get("result") or {}
    runtime = res.get("session_id")
    print("session.resume →", runtime, "| keys:", sorted(res.keys())[:8])

    if runtime:
        r = client.rpc("session.status", {"session_id": runtime})
        print("session.status:", json.dumps(r.get("result") or r.get("error"), ensure_ascii=False)[:400])


if __name__ == "__main__":
    main()
