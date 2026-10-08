"""Filter rules, checked against a stand-in for herdr's socket.

Run: python3 -m unittest discover -s contrib/local-agents/tests
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import herdr_filter  # noqa: E402
import herdr_team  # noqa: E402

MEMBERS = {"ui-designer": "w1:p1", "coder": "w1:p2", "reviewer": "w1:p3"}
FLOW = ["ui-designer", "coder", "reviewer"]


class FilterTest(unittest.TestCase):
    def setUp(self):
        self.runtime = tempfile.TemporaryDirectory()
        patcher = mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": self.runtime.name})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.runtime.cleanup)
        self.team_dir = herdr_team.create("w1", "feature-x", "Build it.", FLOW, MEMBERS, 1)
        self.sent = []
        self.notes = []
        notify = mock.patch.object(herdr_team, "notify", lambda *a, **k: self.notes.append(a))
        notify.start()
        self.addCleanup(notify.stop)

    def member(self, role):
        f = herdr_filter.Filter("/unused.sock", MEMBERS[role], self.team_dir, role)

        def fake_forward(line):
            request = json.loads(line)
            self.sent.append(request)
            return (json.dumps({"id": request["id"], "result": {"type": "ok"}}) + "\n").encode()

        f.forward = fake_forward
        return f

    def call(self, f, method, params):
        line = json.dumps({"id": "t", "method": method, "params": params}).encode()
        return json.loads(f.handle(line))

    def test_send_tags_the_sender_and_targets_the_teammates_pane(self):
        answer = self.call(self.member("ui-designer"), "team.send", {"to": "coder", "text": "design ready"})
        self.assertEqual(answer["result"], {"delivered_to": "coder"})
        prompt = self.sent[-1]
        self.assertEqual(prompt["method"], "agent.prompt")
        self.assertEqual(prompt["params"]["target"], "w1:p2")
        self.assertTrue(prompt["params"]["text"].startswith("[team message from ui-designer] design ready"))
        self.assertEqual(herdr_team.read_state(self.team_dir)["active"], "coder")

    def test_only_teammates_can_be_messaged(self):
        reviewer = self.member("reviewer")
        self.assertEqual(self.call(reviewer, "team.send", {"to": "w9:p1", "text": "x"})["error"]["code"], "unknown_role")
        self.assertEqual(self.call(reviewer, "team.send", {"to": "reviewer", "text": "x"})["error"]["code"], "invalid")
        self.assertEqual(self.sent, [])

    def test_review_rounds_are_capped(self):
        reviewer = self.member("reviewer")
        self.assertIn("result", self.call(reviewer, "team.send", {"to": "coder", "text": "fix 1"}))
        refused = self.call(reviewer, "team.send", {"to": "coder", "text": "fix 2"})
        self.assertEqual(refused["error"]["code"], "round_limit")
        # Forward messages are not rounds.
        self.assertIn("result", self.call(self.member("coder"), "team.send", {"to": "reviewer", "text": "fixed"}))

    def test_raw_herdr_methods_stay_blocked(self):
        coder = self.member("coder")
        for method, params in [
            ("pane.run", {"pane_id": "w1:p2", "command": "id"}),
            ("agent.prompt", {"target": "w1:p1", "text": "x"}),
            ("pane.report_agent", {"pane_id": "w1:p1", "state": "idle"}),
        ]:
            self.assertEqual(self.call(coder, method, params)["error"]["code"], "forbidden", method)
        self.assertEqual(self.sent, [])
        # Its own pane's report still goes through.
        self.assertIn("result", self.call(coder, "pane.report_agent", {"pane_id": "w1:p2", "state": "idle"}))

    def test_done_notifies_and_ends_the_team(self):
        self.call(self.member("reviewer"), "team.done", {"summary": "All good <b>"})
        self.assertTrue(herdr_team.read_state(self.team_dir)["done"])
        self.assertEqual(len(self.notes), 1)
        refused = self.call(self.member("coder"), "team.send", {"to": "reviewer", "text": "late"})
        self.assertEqual(refused["error"]["code"], "finished")

    def test_without_a_team_only_reports_pass(self):
        f = herdr_filter.Filter("/unused.sock", "w1:p2")
        answer = json.loads(f.handle(json.dumps({"id": "t", "method": "team.info", "params": {}}).encode()))
        self.assertEqual(answer["error"]["code"], "forbidden")


if __name__ == "__main__":
    unittest.main()
