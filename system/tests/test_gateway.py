import json
import stat
import tempfile
import unittest
from pathlib import Path

from gateway.runner import ClaudeRunner, parse_output
from gateway.state import Sessions
from gateway.telegram import chunk, to_message


class ChunkTests(unittest.TestCase):
    def test_short_text_is_one_piece(self):
        self.assertEqual(chunk("hello"), ["hello"])

    def test_long_text_is_split_under_limit(self):
        text = "\n".join(["line " * 20] * 200)
        pieces = chunk(text, 500)
        self.assertGreater(len(pieces), 1)
        self.assertTrue(all(len(p) <= 500 for p in pieces))
        self.assertEqual("".join("".join(pieces).split()), "".join(text.split()))

    def test_empty_text_gets_placeholder(self):
        self.assertEqual(chunk("  "), ["(empty reply)"])


class MessageTests(unittest.TestCase):
    def test_topic_message_key(self):
        update = {"update_id": 5, "message": {"text": "hi", "from": {"id": 9}, "chat": {"id": 1},
                                              "is_topic_message": True, "message_thread_id": 7}}
        msg = to_message(update)
        self.assertEqual((msg.key, msg.user_id, msg.thread_id), ("1:7", 9, 7))

    def test_non_text_is_ignored(self):
        self.assertIsNone(to_message({"update_id": 1, "message": {"from": {"id": 1}, "chat": {"id": 1}}}))


class SessionTests(unittest.TestCase):
    def test_set_get_clear(self):
        with tempfile.TemporaryDirectory() as d:
            s = Sessions(Path(d))
            self.assertIsNone(s.get("a"))
            s.set("a", "sid1")
            self.assertEqual(Sessions(Path(d)).get("a"), "sid1")  # survives a new object (restart)
            s.clear("a")
            self.assertIsNone(s.get("a"))


class RunnerTests(unittest.TestCase):
    def test_parse_ok(self):
        out = json.dumps({"result": " hi ", "session_id": "s1", "usage": {"input_tokens": 2, "output_tokens": 3}})
        r = parse_output(out, 0, "", None)
        self.assertEqual((r.status, r.text, r.session_id, r.output_tokens), ("ok", "hi", "s1", 3))

    def test_parse_garbage_is_error(self):
        r = parse_output("not json", 1, "boom", "old")
        self.assertEqual((r.status, r.session_id), ("error", "old"))
        self.assertIn("boom", r.text)

    def test_runner_with_fake_claude(self):
        with tempfile.TemporaryDirectory() as d:
            fake = Path(d) / "claude"
            fake.write_text('#!/bin/sh\ncat >/dev/null\necho \'{"result":"pong","session_id":"abc","usage":{}}\'\n')
            fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
            r = ClaudeRunner(str(fake)).run("ping", Path(d))
            self.assertEqual((r.status, r.text, r.session_id), ("ok", "pong", "abc"))

    def test_missing_binary(self):
        r = ClaudeRunner("/nonexistent/claude").run("x", Path("."))
        self.assertEqual(r.status, "error")

    def test_task_mode_not_ready(self):
        with self.assertRaises(NotImplementedError):
            ClaudeRunner().run("x", Path("."), mode="task")


if __name__ == "__main__":
    unittest.main()
