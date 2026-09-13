import asyncio
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
PLUGIN_API = ROOT / "desktop-plugin" / "dashboard" / "plugin_api.py"

from collector import Collector
from receiver import RelayStore
from feishu_relay import encode_reply, encode_request


def load_plugin_api():
    spec = importlib.util.spec_from_file_location("minis_bridge_plugin_api", PLUGIN_API)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def message(message_id: str, text: str, created: int) -> dict:
    return {
        "message_id": message_id,
        "create_time": str(created),
        "sender": {"sender_type": "app"},
        "body": {"content": json.dumps({"text": text}, ensure_ascii=False)},
    }


class FakeClient:
    def __init__(self, items):
        self.items = items
        self.sent = []

    def list_messages(self, page_size=50):
        return list(self.items)

    def send_text(self, text):
        self.sent.append(text)
        return "om_sent"


class PluginApiTests(unittest.TestCase):
    def setUp(self):
        from collector import serve_in_thread

        self.td = tempfile.TemporaryDirectory()
        route = {"node": "windows-desktop", "profile": "default", "session_id": "sess-A"}
        items = [
            message("m1", encode_request("req-1", "q", route), 1),
            message("m2", encode_reply("req-1", "answer"), 2),
        ]
        self.client = FakeClient(items)
        self.store = RelayStore(Path(self.td.name) / "receiver.db")
        self.collector = Collector(self.client, self.store)
        self.collector.poll_once()
        self.server, self.port = serve_in_thread(self.collector, token="tok", port=0)
        self.env = {
            "MINIS_COLLECTOR_URL": f"http://127.0.0.1:{self.port}",
            "MINIS_COLLECTOR_TOKEN": "tok",
        }

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.store.close()
        self.td.cleanup()

    def test_pending_proxies_collector_events(self):
        api = load_plugin_api()
        with patch.dict(os.environ, self.env, clear=False):
            result = asyncio.run(api.pending())
        self.assertEqual(len(result["events"]), 1)
        self.assertEqual(result["events"][0]["session_id"], "sess-A")

    def test_pending_without_config_returns_empty_not_error(self):
        api = load_plugin_api()
        env = {
            "MINIS_COLLECTOR_URL": "",
            "MINIS_COLLECTOR_TOKEN": "",
            "HERMES_HOME": str(Path(self.td.name) / "no-such-home"),
        }
        with patch.dict(os.environ, env, clear=False):
            result = asyncio.run(api.pending())
        self.assertEqual(result, {"events": [], "configured": False})

    def test_pending_bypasses_http_proxy_env(self):
        api = load_plugin_api()
        env = dict(self.env)
        env["HTTP_PROXY"] = "http://127.0.0.1:1"  # black hole
        env["ALL_PROXY"] = "http://127.0.0.1:1"
        with patch.dict(os.environ, env, clear=True):
            result = asyncio.run(api.pending())
        self.assertEqual(len(result["events"]), 1)

    def test_deliver_confirms_event(self):
        api = load_plugin_api()
        with patch.dict(os.environ, self.env, clear=False):
            claimed = asyncio.run(api.claim({"event_id": "reply:req-1"}))
        self.assertTrue(claimed["ok"])
        with patch.dict(os.environ, self.env, clear=False):
            result = asyncio.run(api.deliver({"event_id": "reply:req-1"}))
        self.assertTrue(result["ok"])
        with patch.dict(os.environ, self.env, clear=False):
            result = asyncio.run(api.pending())
        self.assertEqual(result["events"], [])
class V2RpcTests(unittest.TestCase):
    def test_rpc_accepts_synchronous_result(self):
        # Covered by the real HTTP contract: session.create returns result
        # inline, not via SSE. V2Rpc must not wait for a nonexistent event.
        fake = type('Fake', (), {'read': lambda self: b'{"jsonrpc":"2.0","id":"x","result":{"ok":true}}', '__enter__': lambda self: self, '__exit__': lambda *a: None})()
        api = load_plugin_api()
        with patch.object(api._NO_PROXY_OPENER, 'open', return_value=fake):
            c = object.__new__(api.V2Rpc)
            c.base = 'http://test'; c.token = 't'; c.client_id = 'c'; c.responses = {}; c._lock = __import__('threading').Lock()
            result = c.rpc('session.create', {})
        self.assertEqual(result['result']['ok'], True)


class FakeRpc:
    def __init__(self, running=False, fail_submit=False, sessions=None):
        self.running = running
        self.fail_submit = fail_submit
        self.submitted = []
        self.sessions = sessions or []
        self.created = []
        self.titled = []

    def rpc(self, method, params, timeout=30):
        if method == "session.list":
            return {"result": {"sessions": self.sessions}}
        if method == "session.create":
            self.created.append(params)
            return {"result": {"session_id": "runtime-new"}}
        if method == "session.title":
            self.titled.append(params)
            return {"result": {}}
        if method == "session.resume":
            return {"result": {"session_id": "runtime-1", "running": self.running}}
        if method == "prompt.submit":
            if self.fail_submit:
                return {"error": {"code": 5000, "message": "boom"}}
            self.submitted.append(params)
            return {"result": {"status": "streaming"}}
        return {"result": {}}


class EnsureInboxTests(unittest.TestCase):
    def setUp(self):
        self.api = load_plugin_api()
        self.state = tempfile.NamedTemporaryFile(delete=False)
        self.state.close()
        os.unlink(self.state.name)
        self.api.INBOX_STATE = self.state.name

    def tearDown(self):
        try: os.unlink(self.state.name)
        except FileNotFoundError: pass

    def test_reuses_existing_inbox_by_title(self):
        rpc = FakeRpc(sessions=[{"id": "stored-inbox", "title": "📱 Minis 收件箱"}])
        with patch.object(self.api, "_make_rpc_client", return_value=rpc):
            result = asyncio.run(self.api.ensure_inbox({}))
        self.assertEqual(result["session_id"], "stored-inbox")
        self.assertEqual(rpc.created, [])

    def test_creates_and_titles_inbox_when_missing(self):
        rpc = FakeRpc()
        with patch.object(self.api, "_make_rpc_client", return_value=rpc):
            result = asyncio.run(self.api.ensure_inbox({}))
        self.assertEqual(result["session_id"], "runtime-new")
        self.assertEqual(rpc.titled[0]["title"], "📱 Minis 收件箱")


class DeliverSubmitTests(unittest.TestCase):
    def setUp(self):
        self.api = load_plugin_api()

    def test_submit_happy_path(self):
        rpc = FakeRpc()
        with patch.object(self.api, "_make_rpc_client", return_value=rpc):
            result = asyncio.run(self.api.submit({"session_id": "sess-A", "text": "hi"}))
        self.assertEqual(result["status"], "submitted")
        self.assertEqual(result["runtime"], "runtime-1")
        self.assertEqual(rpc.submitted[0]["text"], "hi")
        self.assertEqual(rpc.submitted[0]["session_id"], "runtime-1")

    def test_busy_session_is_not_interrupted(self):
        rpc = FakeRpc(running=True)
        with patch.object(self.api, "_make_rpc_client", return_value=rpc):
            result = asyncio.run(self.api.submit({"session_id": "sess-A", "text": "hi"}))
        self.assertEqual(result["status"], "busy")
        self.assertEqual(rpc.submitted, [])

    def test_submit_error_surfaces(self):
        rpc = FakeRpc(fail_submit=True)
        with patch.object(self.api, "_make_rpc_client", return_value=rpc):
            result = asyncio.run(self.api.submit({"session_id": "sess-A", "text": "hi"}))
        self.assertEqual(result["status"], "error")


if __name__ == "__main__":
    unittest.main()
