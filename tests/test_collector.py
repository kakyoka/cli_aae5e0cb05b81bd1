import http.client
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from feishu_relay import decode_envelope, encode_reply, encode_request
from receiver import RelayStore


def message(message_id: str, text: str, created: int) -> dict:
    return {
        "message_id": message_id,
        "create_time": str(created),
        "sender": {"sender_type": "app"},
        "body": {"content": json.dumps({"text": text}, ensure_ascii=False)},
    }


class FakeClient:
    def __init__(self, items=None):
        self.items = list(items or [])
        self.sent = []

    def list_messages(self, page_size=50):
        return list(self.items)

    def send_text(self, text):
        self.sent.append(text)
        return "om_sent"


class CollectorApiTests(unittest.TestCase):
    def setUp(self):
        from collector import Collector, serve_in_thread

        self.td = tempfile.TemporaryDirectory()
        route = {"node": "windows-desktop", "profile": "default", "session_id": "sess-A"}
        self.items = [
            message("m1", encode_request("req-1", "question", route), 1),
            message("m2", encode_reply("req-1", "answer body"), 2),
        ]
        self.client = FakeClient(self.items)
        self.store = RelayStore(Path(self.td.name) / "receiver.db")
        self.collector = Collector(self.client, self.store)
        self.collector.poll_once()
        self.server, self.port = serve_in_thread(self.collector, token="test-token", port=0)

    def tearDown(self):
        server = getattr(self, "server", None)
        if server is not None:
            server.shutdown()
            server.server_close()
        store = getattr(self, "store", None)
        if store is not None:
            store.close()
        self.td.cleanup()

    def _get(self, path, token=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        conn.request("GET", path, headers=headers)
        resp = conn.getresponse()
        body = resp.read().decode()
        conn.close()
        return resp.status, body

    def _post(self, path, payload, token=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        conn.request("POST", path, json.dumps(payload), headers)
        resp = conn.getresponse()
        body = resp.read().decode()
        conn.close()
        return resp.status, body

    def test_health_is_public(self):
        status, body = self._get("/health")
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)["ok"])

    def test_pending_requires_auth(self):
        status, _ = self._get("/api/pending?node=windows-desktop")
        self.assertEqual(status, 401)

    def test_pending_returns_events_for_node(self):
        status, body = self._get("/api/pending?node=windows-desktop", token="test-token")
        self.assertEqual(status, 200)
        events = json.loads(body)["events"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event_id"], "reply:req-1")
        self.assertEqual(events[0]["session_id"], "sess-A")
        self.assertEqual(events[0]["body"], "answer body")

    def test_pending_excludes_other_nodes(self):
        status, body = self._get("/api/pending?node=kanas", token="test-token")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["events"], [])

    def test_deliver_marks_event_and_is_idempotent(self):
        status, body = self._post("/api/claim", {"event_id": "reply:req-1"}, token="test-token")
        self.assertEqual(status, 200)
        status, body = self._post("/api/deliver", {"event_id": "reply:req-1"}, token="test-token")
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)["ok"])
        status, body = self._get("/api/pending?node=windows-desktop", token="test-token")
        self.assertEqual(json.loads(body)["events"], [])
        status, body = self._post("/api/deliver", {"event_id": "reply:req-1"}, token="test-token")
        self.assertEqual(status, 409)

    def test_push_delivery_sends_feishu_ack(self):
        from feishu_relay import encode_push

        self.client.items.append(message("m3", encode_push("push-9", "hello"), 3))
        self.collector.poll_once()
        status, body = self._get("/api/pending?node=windows-desktop", token="test-token")
        events = json.loads(body)["events"]
        self.assertTrue(any(e["event_id"] == "push:push-9" for e in events))
        status, body = self._post("/api/claim", {"event_id": "push:push-9"}, token="test-token")
        self.assertEqual(status, 200)
        status, body = self._post("/api/deliver", {"event_id": "push:push-9"}, token="test-token")
        self.assertEqual(status, 200)
        self.assertTrue(self.client.sent)
        self.assertEqual(decode_envelope(self.client.sent[0]).kind, "push_ack")
        self.assertEqual(decode_envelope(self.client.sent[0]).request_id, "push-9")


if __name__ == "__main__":
    unittest.main()
