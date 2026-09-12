import io
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from feishu_relay import (
    decode_envelope,
    encode_push,
    encode_push_ack,
    encode_reply,
    encode_request,
    find_pending_pushes,
    find_pending_requests,
    find_reply,
    main,
)


class EnvelopeTests(unittest.TestCase):
    def test_request_round_trip_preserves_id_and_prompt(self):
        text = encode_request("req-001", "请总结今天的 Apple 新闻")
        env = decode_envelope(text)
        self.assertEqual(env.kind, "request")
        self.assertEqual(env.request_id, "req-001")
        self.assertEqual(env.body, "请总结今天的 Apple 新闻")

    def test_pending_filter_ignores_non_relay_and_already_replied(self):
        def msg(text, created, sender="app"):
            return {
                "create_time": str(created),
                "sender": {"sender_type": sender},
                "body": {"content": __import__("json").dumps({"text": text})},
            }

        items = [
            msg("普通机器人通知", 1),
            msg(encode_request("req-old", "旧任务"), 2),
            msg(encode_reply("req-old", "已完成"), 3),
            msg(encode_request("req-new", "新任务"), 4),
            msg(encode_request("req-user", "不应接单"), 5, sender="user"),
        ]
        pending = find_pending_requests(items)
        self.assertEqual([(x.request_id, x.body) for x in pending], [("req-new", "新任务")])

    def test_send_cli_posts_request_and_prints_id(self):
        class FakeClient:
            def __init__(self):
                self.sent = []

            def send_text(self, text):
                self.sent.append(text)
                return "om_test"

        client = FakeClient()
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            rc = main(["--send", "ping", "--id", "req-fixed"], client=client)
        self.assertEqual(rc, 0)
        self.assertEqual(stdout.getvalue().strip(), "req-fixed")
        self.assertEqual(decode_envelope(client.sent[0]).body, "ping")

    def test_pull_cli_returns_oldest_pending_request_as_json(self):
        class FakeClient:
            def list_messages(self):
                def msg(text, created):
                    return {
                        "create_time": str(created),
                        "sender": {"sender_type": "app"},
                        "body": {"content": __import__("json").dumps({"text": text})},
                    }
                return [msg(encode_request("req-2", "second"), 2), msg(encode_request("req-1", "first"), 1)]

        stdout = io.StringIO()
        with redirect_stdout(stdout):
            rc = main(["--pull"], client=FakeClient())
        self.assertEqual(rc, 0)
        self.assertEqual(__import__("json").loads(stdout.getvalue()), {"id": "req-1", "body": "first"})

    def test_reply_cli_posts_reply_with_same_request_id(self):
        class FakeClient:
            def __init__(self):
                self.sent = []

            def list_messages(self):
                return []

            def send_text(self, text):
                self.sent.append(text)
                return "om_reply"

        client = FakeClient()
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            rc = main(["--reply", "req-1", "--text", "处理完成"], client=client)
        self.assertEqual(rc, 0)
        env = decode_envelope(client.sent[0])
        self.assertEqual((env.kind, env.request_id, env.body), ("reply", "req-1", "处理完成"))

    def test_reply_cli_is_idempotent_when_reply_already_exists(self):
        class FakeClient:
            def __init__(self):
                self.sent = []

            def list_messages(self):
                return [{
                    "create_time": "1",
                    "sender": {"sender_type": "app"},
                    "body": {"content": __import__("json").dumps({"text": encode_reply("req-1", "first")})},
                }]

            def send_text(self, text):
                self.sent.append(text)
                return "om_duplicate"

        client = FakeClient()
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            rc = main(["--reply", "req-1", "--text", "second"], client=client)
        self.assertEqual(rc, 0)
        self.assertEqual(client.sent, [])
        self.assertIn("already replied", stdout.getvalue())

    def test_read_cli_prints_matching_reply_only(self):
        class FakeClient:
            def list_messages(self):
                def msg(text, created):
                    return {
                        "create_time": str(created),
                        "sender": {"sender_type": "app"},
                        "body": {"content": __import__("json").dumps({"text": text})},
                    }
                return [msg(encode_reply("other", "wrong"), 2), msg(encode_reply("wanted", "right"), 1)]

        stdout = io.StringIO()
        with redirect_stdout(stdout):
            rc = main(["--read", "wanted"], client=FakeClient())
        self.assertEqual(rc, 0)
        self.assertEqual(stdout.getvalue().strip(), "right")

    def test_pending_push_filter_excludes_acknowledged_push(self):
        def msg(text, created):
            return {
                "create_time": str(created),
                "sender": {"sender_type": "app"},
                "body": {"content": __import__("json").dumps({"text": text})},
            }
        items = [
            msg(encode_push("push-old", "old"), 1),
            msg(encode_push_ack("push-old"), 2),
            msg(encode_push("push-new", "hello Hermes"), 3),
        ]
        pending = find_pending_pushes(items)
        self.assertEqual([(x.request_id, x.body) for x in pending], [("push-new", "hello Hermes")])

    def test_push_and_pull_push_cli(self):
        class PushClient:
            def __init__(self):
                self.sent = []
            def send_text(self, text):
                self.sent.append(text)
                return "om_push"

        sender = PushClient()
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            rc = main(["--push", "Minis 主动消息", "--id", "push-fixed"], client=sender)
        self.assertEqual(rc, 0)
        self.assertEqual(stdout.getvalue().strip(), "push-fixed")
        self.assertEqual(decode_envelope(sender.sent[0]).kind, "push")

        class PullClient:
            def list_messages(self):
                return [{
                    "create_time": "1",
                    "sender": {"sender_type": "app"},
                    "body": {"content": __import__("json").dumps({"text": sender.sent[0]})},
                }]
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            rc = main(["--pull-push"], client=PullClient())
        self.assertEqual(rc, 0)
        self.assertEqual(__import__("json").loads(stdout.getvalue()), {"id": "push-fixed", "body": "Minis 主动消息"})

    def test_ack_push_cli_posts_ack(self):
        class FakeClient:
            def __init__(self):
                self.sent = []
            def list_messages(self):
                return []
            def send_text(self, text):
                self.sent.append(text)
                return "om_ack"
        client = FakeClient()
        rc = main(["--ack-push", "push-1"], client=client)
        self.assertEqual(rc, 0)
        self.assertEqual(decode_envelope(client.sent[0]).kind, "push_ack")


if __name__ == "__main__":
    unittest.main()
