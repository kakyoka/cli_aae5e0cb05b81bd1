import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from feishu_relay import encode_reply, encode_request
from receiver import RelayStore


def message(message_id: str, text: str, created: int) -> dict:
    return {
        "message_id": message_id,
        "create_time": str(created),
        "sender": {"sender_type": "app"},
        "body": {"content": json.dumps({"text": text}, ensure_ascii=False)},
    }


class RelayStoreTests(unittest.TestCase):
    def test_reply_inherits_request_route_and_is_pending_once(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "receiver.db"
            route = {
                "node": "windows-desktop",
                "profile": "default",
                "session_id": "session-A",
            }
            items = [
                message("m1", encode_request("req-1", "question", route), 1),
                message("m2", encode_reply("req-1", "answer"), 2),
            ]
            store = RelayStore(db)
            self.assertEqual(store.ingest(items), 2)
            self.assertEqual(store.pending("windows-desktop"), [{
                "event_id": "reply:req-1",
                "kind": "reply",
                "request_id": "req-1",
                "body": "answer",
                "node": "windows-desktop",
                "profile": "default",
                "session_id": "session-A",
            }])
            store.close()

            reopened = RelayStore(db)
            self.assertEqual(reopened.pending("windows-desktop"), store_expected := [{
                "event_id": "reply:req-1",
                "kind": "reply",
                "request_id": "req-1",
                "body": "answer",
                "node": "windows-desktop",
                "profile": "default",
                "session_id": "session-A",
            }])
            self.assertTrue(reopened.claim(store_expected[0]["event_id"]))
            self.assertTrue(reopened.mark_delivered(store_expected[0]["event_id"]))
            self.assertEqual(reopened.pending("windows-desktop"), [])
            self.assertFalse(reopened.mark_delivered(store_expected[0]["event_id"]))
            reopened.close()

    def test_duplicate_feishu_messages_do_not_duplicate_delivery(self):
        with tempfile.TemporaryDirectory() as td:
            store = RelayStore(Path(td) / "receiver.db")
            route = {"node": "windows-desktop", "profile": "default", "session_id": "s"}
            items = [message("m1", encode_request("req-2", "q", route), 1)]
            self.assertEqual(store.ingest(items), 1)
            self.assertEqual(store.ingest(items), 0)
            store.close()

    def test_push_ack_in_history_prevents_pending_event(self):
        from feishu_relay import encode_push, encode_push_ack

        with tempfile.TemporaryDirectory() as td:
            store = RelayStore(Path(td) / "receiver.db")
            items = [
                message("m1", encode_push("push-old", "stale"), 1),
                message("m2", encode_push_ack("push-old"), 2),
                message("m3", encode_push("push-new", "fresh"), 3),
            ]
            store.ingest(items)
            pending = store.pending("windows-desktop")
            self.assertEqual([e["event_id"] for e in pending], ["push:push-new"])
            store.close()

    def test_late_push_ack_marks_pending_event_delivered(self):
        from feishu_relay import encode_push, encode_push_ack

        with tempfile.TemporaryDirectory() as td:
            store = RelayStore(Path(td) / "receiver.db")
            store.ingest([message("m1", encode_push("push-x", "hello"), 1)])
            self.assertEqual(len(store.pending("windows-desktop")), 1)
            store.ingest([message("m2", encode_push_ack("push-x"), 2)])
            self.assertEqual(store.pending("windows-desktop"), [])
            store.close()
    def test_claim_is_atomic_and_expires(self):
        import time as _time
        with tempfile.TemporaryDirectory() as td:
            store = RelayStore(Path(td) / "receiver.db")
            route = {"node": "windows-desktop", "profile": "default", "session_id": "s"}
            items = [
                message("m1", encode_request("req-c", "q", route), 1),
                message("m2", encode_reply("req-c", "a"), 2),
            ]
            store.ingest(items)
            self.assertTrue(store.claim("reply:req-c"))
            self.assertFalse(store.claim("reply:req-c"))  # second claim loses
            # claimed event is hidden from pending while the lease is fresh
            self.assertEqual(store.pending("windows-desktop"), [])
            # expired lease requeues automatically
            with store._lock:
                store._con.execute(
                    "UPDATE events SET claimed_at = ? WHERE event_id = ?",
                    (_time.time() - 9999, "reply:req-c"),
                )
                store._con.commit()
            self.assertEqual(len(store.pending("windows-desktop")), 1)
            self.assertTrue(store.claim("reply:req-c"))  # re-claimable after expiry
            store.close()

    def test_deliver_requires_claim_and_release_requeues(self):
        with tempfile.TemporaryDirectory() as td:
            store = RelayStore(Path(td) / "receiver.db")
            route = {"node": "windows-desktop", "profile": "default", "session_id": "s"}
            items = [
                message("m1", encode_request("req-d", "q", route), 1),
                message("m2", encode_reply("req-d", "a"), 2),
            ]
            store.ingest(items)
            # deliver without claim is refused
            self.assertFalse(store.mark_delivered("reply:req-d"))
            self.assertTrue(store.claim("reply:req-d"))
            # submit failed → release requeues
            self.assertTrue(store.release("reply:req-d"))
            self.assertEqual(len(store.pending("windows-desktop")), 1)
            # claim → deliver works
            self.assertTrue(store.claim("reply:req-d"))
            self.assertTrue(store.mark_delivered("reply:req-d"))
            self.assertEqual(store.pending("windows-desktop"), [])
            store.close()


if __name__ == "__main__":
    unittest.main()
