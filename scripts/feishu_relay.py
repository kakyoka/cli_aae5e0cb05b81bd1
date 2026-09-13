#!/usr/bin/env python3
"""Feishu transport for Win Hermes <-> iPhone Open Minis."""

from __future__ import annotations

from dataclasses import dataclass, field
import argparse
import json
import os
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Sequence

REQUEST_PREFIX = "[MINIS_REQ]"
REPLY_PREFIX = "[MINIS_REPLY]"
PUSH_PREFIX = "[MINIS_PUSH]"
PUSH_ACK_PREFIX = "[MINIS_PUSH_ACK]"


@dataclass(frozen=True)
class Envelope:
    kind: str
    request_id: str
    body: str
    route: dict[str, str] = field(default_factory=dict)


def encode_request(request_id: str, prompt: str, route: dict[str, str] | None = None) -> str:
    payload: dict[str, object] = {"id": request_id}
    if route:
        payload["route"] = route
    meta = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return f"{REQUEST_PREFIX}\n{meta}\n\n{prompt}"


def encode_reply(request_id: str, reply: str) -> str:
    meta = json.dumps({"id": request_id}, ensure_ascii=False, separators=(",", ":"))
    return f"{REPLY_PREFIX}\n{meta}\n\n{reply}"


def encode_push(push_id: str, body: str) -> str:
    meta = json.dumps({"id": push_id}, ensure_ascii=False, separators=(",", ":"))
    return f"{PUSH_PREFIX}\n{meta}\n\n{body}"


def encode_push_ack(push_id: str) -> str:
    meta = json.dumps({"id": push_id}, ensure_ascii=False, separators=(",", ":"))
    return f"{PUSH_ACK_PREFIX}\n{meta}\n\nack"


def decode_envelope(text: str) -> Envelope:
    lines = text.splitlines()
    if len(lines) < 3:
        raise ValueError("invalid Minis relay envelope")
    prefix = lines[0].strip()
    if prefix == REQUEST_PREFIX:
        kind = "request"
    elif prefix == REPLY_PREFIX:
        kind = "reply"
    elif prefix == PUSH_PREFIX:
        kind = "push"
    elif prefix == PUSH_ACK_PREFIX:
        kind = "push_ack"
    else:
        raise ValueError("not a Minis relay message")
    meta = json.loads(lines[1])
    if not meta.get("id"):
        raise ValueError("missing request id")
    body = "\n".join(lines[3:] if lines[2] == "" else lines[2:])
    route = meta.get("route")
    if not isinstance(route, dict):
        route = {}
    clean_route = {str(k): str(v) for k, v in route.items() if v is not None}
    return Envelope(kind=kind, request_id=str(meta["id"]), body=body, route=clean_route)


def _message_envelope(item: dict) -> Envelope | None:
    if (item.get("sender") or {}).get("sender_type") != "app":
        return None
    try:
        body = json.loads((item.get("body") or {}).get("content", "{}"))
        return decode_envelope(body.get("text", ""))
    except (ValueError, TypeError, json.JSONDecodeError):
        return None


def find_pending_requests(items: list[dict]) -> list[Envelope]:
    ordered = sorted(items, key=lambda x: int(x.get("create_time", 0)))
    envelopes = [env for item in ordered if (env := _message_envelope(item))]
    replied = {env.request_id for env in envelopes if env.kind == "reply"}
    return [env for env in envelopes if env.kind == "request" and env.request_id not in replied]


def find_reply(items: list[dict], request_id: str) -> Envelope | None:
    ordered = sorted(items, key=lambda x: int(x.get("create_time", 0)), reverse=True)
    for item in ordered:
        env = _message_envelope(item)
        if env and env.kind == "reply" and env.request_id == request_id:
            return env
    return None


def find_pending_pushes(items: list[dict]) -> list[Envelope]:
    ordered = sorted(items, key=lambda x: int(x.get("create_time", 0)))
    envelopes = [env for item in ordered if (env := _message_envelope(item))]
    acknowledged = {env.request_id for env in envelopes if env.kind == "push_ack"}
    return [env for env in envelopes if env.kind == "push" and env.request_id not in acknowledged]


def _read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def load_config() -> dict[str, str]:
    values = dict(os.environ)
    candidates = [
        Path.home() / ".feishu.env",
        Path.home() / ".minis-feishu.env",
    ]
    for path in candidates:
        if path.exists():
            values = {**_read_env_file(path), **values}
    chat = values.get("FEISHU_MINIS_CHAT_ID") or values.get("FEISHU_CHAT_ID") or values.get("CHAT_ID")
    config = {
        "app_id": values.get("FEISHU_APP_ID", ""),
        "app_secret": values.get("FEISHU_APP_SECRET", ""),
        "chat_id": chat or "",
    }
    missing = [key for key, value in config.items() if not value]
    if missing:
        raise RuntimeError("missing Feishu config: " + ", ".join(missing))
    return config


class FeishuClient:
    def __init__(self, app_id: str, app_secret: str, chat_id: str):
        self.app_id = app_id
        self.app_secret = app_secret
        self.chat_id = chat_id
        self._token = ""

    def _request(self, method: str, url: str, body: dict | None = None, auth: bool = True) -> dict:
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if auth:
            headers["Authorization"] = f"Bearer {self.token()}"
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        with urllib.request.urlopen(request, timeout=20) as response:
            result = json.loads(response.read().decode("utf-8"))
        if result.get("code", 0) != 0:
            raise RuntimeError(f"Feishu API error {result.get('code')}: {result.get('msg')}")
        return result

    def token(self) -> str:
        if not self._token:
            result = self._request(
                "POST",
                "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
                {"app_id": self.app_id, "app_secret": self.app_secret},
                auth=False,
            )
            self._token = result["tenant_access_token"]
        return self._token

    def send_text(self, text: str) -> str:
        result = self._request(
            "POST",
            "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id",
            {
                "receive_id": self.chat_id,
                "msg_type": "text",
                "content": json.dumps({"text": text}, ensure_ascii=False),
            },
        )
        return (result.get("data") or {}).get("message_id", "")

    def list_messages(self, page_size: int = 50) -> list[dict]:
        query = urllib.parse.urlencode({
            "container_id_type": "chat",
            "container_id": self.chat_id,
            "page_size": page_size,
            "sort_type": "ByCreateTimeDesc",
        })
        result = self._request("GET", "https://open.feishu.cn/open-apis/im/v1/messages?" + query)
        return (result.get("data") or {}).get("items", [])


def make_client() -> FeishuClient:
    config = load_config()
    return FeishuClient(config["app_id"], config["app_secret"], config["chat_id"])


def main(argv: Sequence[str] | None = None, client=None) -> int:
    parser = argparse.ArgumentParser(description="Win Hermes <-> iPhone Minis over Feishu")
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument("--send", metavar="TEXT")
    actions.add_argument("--pull", action="store_true")
    actions.add_argument("--reply", metavar="REQUEST_ID")
    actions.add_argument("--read", metavar="REQUEST_ID")
    actions.add_argument("--push", metavar="TEXT")
    actions.add_argument("--pull-push", action="store_true")
    actions.add_argument("--ack-push", metavar="PUSH_ID")
    parser.add_argument("--id", dest="request_id")
    parser.add_argument("--text", metavar="TEXT")
    args = parser.parse_args(argv)
    active_client = client or make_client()
    if args.pull_push:
        pending = find_pending_pushes(active_client.list_messages())
        if not pending:
            print("null")
            return 3
        env = pending[0]
        print(json.dumps({"id": env.request_id, "body": env.body}, ensure_ascii=False))
        return 0
    if args.ack_push:
        active_client.send_text(encode_push_ack(args.ack_push))
        print(args.ack_push)
        return 0
    if args.push:
        push_id = args.request_id or f"push-{int(time.time())}"
        active_client.send_text(encode_push(push_id, args.push))
        print(push_id)
        return 0
    if args.read:
        reply = find_reply(active_client.list_messages(), args.read)
        if not reply:
            print("(no reply yet)")
            return 3
        print(reply.body)
        return 0
    if args.reply:
        if args.text is None:
            parser.error("--reply requires --text")
        existing = find_reply(active_client.list_messages(), args.reply)
        if existing:
            print(f"{args.reply} already replied")
            return 0
        active_client.send_text(encode_reply(args.reply, args.text))
        print(args.reply)
        return 0
    if args.pull:
        pending = find_pending_requests(active_client.list_messages())
        if not pending:
            print("null")
            return 3
        env = pending[0]
        print(json.dumps({"id": env.request_id, "body": env.body}, ensure_ascii=False))
        return 0
    request_id = args.request_id or f"req-{int(time.time())}"
    session_id = os.environ.get("HERMES_SESSION_ID", "").strip()
    route = {}
    if session_id:
        route = {
            "node": os.environ.get("MINIS_BRIDGE_NODE", "windows-desktop").strip() or "windows-desktop",
            "profile": os.environ.get("HERMES_SESSION_PROFILE", "").strip() or "default",
            "session_id": session_id,
        }
    active_client.send_text(encode_request(request_id, args.send, route=route))
    print(request_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
