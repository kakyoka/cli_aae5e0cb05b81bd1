"""Minis bridge plugin backend — namespace /api/plugins/minis-bridge.

Server-side proxy to the Mac mini relay collector. The collector token stays
in this backend process (read from env or $HERMES_HOME/.env); the desktop
renderer never sees it.
"""

import asyncio
import json
import os
import re
import threading
import time
import urllib.request
from pathlib import Path

try:
    from fastapi import APIRouter
except ImportError:  # standalone unit tests
    class APIRouter:  # minimal shim with the same decorator surface
        def get(self, _path):
            return lambda fn: fn

        def post(self, _path):
            return lambda fn: fn


router = APIRouter()


def _read_env_file(path: Path) -> dict:
    values = {}
    try:
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip().strip('"').strip("'")
    except OSError:
        pass
    return values


def collector_config() -> tuple[str, str]:
    url = os.environ.get("MINIS_COLLECTOR_URL", "").strip()
    token = os.environ.get("MINIS_COLLECTOR_TOKEN", "").strip()
    if not url or not token:
        home = Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes")))
        values = _read_env_file(home / ".env")
        url = url or values.get("MINIS_COLLECTOR_URL", "").strip()
        token = token or values.get("MINIS_COLLECTOR_TOKEN", "").strip()
    return url, token


def node_name() -> str:
    return os.environ.get("MINIS_BRIDGE_NODE", "windows-desktop").strip() or "windows-desktop"


_NO_PROXY_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _http_json(method: str, url: str, token: str, body: dict | None = None,
               timeout: float = 8) -> dict:
    """HTTP call that ALWAYS bypasses system/env proxies.

    The collector lives on the LAN/Tailscale (Mac mini). A Clash-style
    HTTP_PROXY in the gateway process env would route these private
    addresses into the proxy and hang — exactly the failure this avoids.
    """
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    with _NO_PROXY_OPENER.open(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


@router.get("/pending")
async def pending():
    url, token = collector_config()
    if not url or not token:
        return {"events": [], "configured": False}
    try:
        return _http_json("GET", f"{url}/api/pending?node={node_name()}", token)
    except Exception as exc:
        return {"events": [], "configured": True, "error": str(exc)}


@router.post("/deliver")
async def deliver(body: dict):
    return await _proxy_post("/api/deliver", body)


@router.post("/claim")
async def claim(body: dict):
    return await _proxy_post("/api/claim", body)


@router.post("/release")
async def release(body: dict):
    return await _proxy_post("/api/release", body)


async def _proxy_post(path: str, body: dict):
    url, token = collector_config()
    if not url or not token:
        return {"ok": False, "configured": False, "error": "collector not configured"}
    try:
        return _http_json("POST", f"{url}{path}", token, body=body)
    except Exception as exc:
        return {"ok": False, "configured": True, "error": str(exc)}


# ── Hermes v2 RPC (deliver into an existing session) ──────────────────────

_V2_BASE = os.environ.get("MINIS_HERMES_V2_BASE", "http://127.0.0.1:9120")


def _dashboard_token() -> str:
    html = _NO_PROXY_OPENER.open(_V2_BASE + "/", timeout=8).read().decode("utf-8", "ignore")
    match = re.search(r'__HERMES_SESSION_TOKEN__\s*=\s*["\']([^"\']+)', html)
    if not match:
        raise RuntimeError("session token not found in dashboard HTML")
    return match.group(1)


class V2Rpc:
    """Minimal client for the dashboard's /api/v2/events + /api/v2/rpc door."""

    def __init__(self, base: str | None = None, token: str | None = None):
        self.base = base or _V2_BASE
        self.token = token or _dashboard_token()
        self.client_id = ""
        self.responses = {}
        self._lock = threading.Lock()
        threading.Thread(target=self._listen, daemon=True).start()
        deadline = time.time() + 10
        while not self.client_id and time.time() < deadline:
            time.sleep(0.05)
        if not self.client_id:
            raise RuntimeError("no client_id from /api/v2/events")

    def _listen(self):
        resp = _NO_PROXY_OPENER.open(self.base + "/api/v2/events?token=" + self.token, timeout=600)
        while True:
            line = resp.readline()
            if not line:
                break
            s = line.decode("utf-8", "ignore").strip()
            if not s.startswith("data:"):
                continue
            try:
                payload = json.loads(s[5:].strip())
            except ValueError:
                continue
            if "client_id" in payload:
                self.client_id = payload["client_id"]
            elif payload.get("jsonrpc") == "2.0" and "id" in payload:
                with self._lock:
                    self.responses[payload["id"]] = payload

    def rpc(self, method: str, params: dict, timeout: float = 30) -> dict:
        rid = f"minis-bridge-{time.time_ns()}"
        req = urllib.request.Request(
            self.base + "/api/v2/rpc",
            data=json.dumps({"jsonrpc": "2.0", "id": rid, "method": method, "params": params}).encode(),
            headers={
                "Content-Type": "application/json",
                "X-Hermes-Session-Token": self.token,
                "X-Hermes-Client-Id": self.client_id,
            },
            method="POST",
        )
        ack = json.loads(_NO_PROXY_OPENER.open(req, timeout=20).read().decode())
        # CN Desktop may complete a method synchronously in the HTTP body,
        # or return {accepted:true, async:true} and publish the result on SSE.
        if "error" in ack:
            return ack
        ack_result = ack.get("result")
        if "result" in ack and not (isinstance(ack_result, dict) and ack_result.get("async")):
            return ack
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self._lock:
                if rid in self.responses:
                    return self.responses.pop(rid)
            time.sleep(0.1)
        raise TimeoutError(f"rpc {method} timed out")


def _make_rpc_client():
    return V2Rpc()


INBOX_TITLE = "📱 Minis 收件箱"
INBOX_STATE = os.path.join(os.path.expanduser("~"), ".hermes-minis-inbox.json")


@router.post("/ensure-inbox")
async def ensure_inbox(body: dict):
    """Find (or lazily create) the dedicated session for unrouted Minis pushes."""
    return await asyncio.to_thread(_ensure_inbox_sync, body)


def _ensure_inbox_sync(body: dict):
    try:
        client = _make_rpc_client()
    except Exception as exc:
        return {"status": "error", "error": f"rpc connect failed: {exc}"}
    try:
        if os.path.exists(INBOX_STATE):
            with open(INBOX_STATE, "r", encoding="utf-8") as fh:
                saved = json.load(fh)
            stored = str(saved.get("stored_session_id", "")).strip()
            if stored:
                resumed = client.rpc("session.resume", {"session_id": stored}, timeout=30)
                runtime = (resumed.get("result") or {}).get("session_id")
                if runtime:
                    return {"status": "ok", "session_id": stored}
        listed = client.rpc("session.list", {"limit": 500}, timeout=30)
        for s in (listed.get("result") or {}).get("sessions") or []:
            if s.get("title") == INBOX_TITLE and s.get("id"):
                return {"status": "ok", "session_id": s["id"]}
        created = client.rpc("session.create", {}, timeout=30)
        runtime = (created.get("result") or {}).get("session_id")
        if not runtime:
            return {"status": "error", "error": "session.create returned no session_id"}
        client.rpc("session.title", {"session_id": runtime, "title": INBOX_TITLE}, timeout=30)
        stored = (created.get("result") or {}).get("stored_session_id")
        if stored:
            tmp = INBOX_STATE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump({"stored_session_id": stored}, fh)
            os.replace(tmp, INBOX_STATE)
        return {"status": "ok", "session_id": stored or runtime}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


@router.post("/submit")
async def submit(body: dict):
    """Resume a stored session and submit text into it via official RPC.

    Never interrupts a running turn: a busy session returns status=busy and
    the caller keeps the event queued for a later poll.
    """
    return await asyncio.to_thread(_submit_sync, body)


def _submit_sync(body: dict):
    session_id = str(body.get("session_id", "")).strip()
    text = str(body.get("text", ""))
    if not session_id or not text:
        return {"status": "error", "error": "session_id and text required"}
    try:
        client = _make_rpc_client()
    except Exception as exc:
        return {"status": "error", "error": f"rpc connect failed: {exc}"}
    try:
        resumed = client.rpc("session.resume", {"session_id": session_id, "lazy": True}, timeout=60)
        if "error" in resumed:
            return {"status": "error", "error": f"resume: {resumed['error'].get('message')}"}
        result = resumed.get("result") or {}
        runtime = result.get("session_id")
        if not runtime:
            return {"status": "error", "error": "resume returned no session_id"}
        # session.resume is authoritative here. The runtime handle returned
        # by CN Desktop is for prompt.submit; session.status is a different
        # notification path and may not emit a response for this handle.
        if result.get("running"):
            return {"status": "busy", "runtime": runtime}
        submitted = client.rpc("prompt.submit", {"session_id": runtime, "text": text}, timeout=120)
        if "error" in submitted:
            return {"status": "error", "runtime": runtime,
                    "error": f"submit: {submitted['error'].get('message')}"}
        return {"status": "submitted", "runtime": runtime}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}
