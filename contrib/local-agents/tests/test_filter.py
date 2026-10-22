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
import herdr_flow  # noqa: E402
import herdr_team  # noqa: E402

MEMBERS = {"planner": "w1:p1", "coder": "w1:p2", "reviewer": "w1:p3"}
TEAM = {
    "start": "plan",
    "stages": {
        "plan": {"role": "planner", "next": "build"},
        "build": {"role": "coder", "check": "check", "pass": "review", "fail": "build"},
        "review": {"role": "reviewer", "next": "finish", "findings": "build"},
    },
}


class FilterTest(unittest.TestCase):
    def setUp(self):
        self.runtime = tempfile.TemporaryDirectory()
        patcher = mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": self.runtime.name, "XDG_DATA_HOME": self.runtime.name})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.runtime.cleanup)
        flow = herdr_flow.parse(TEAM, list(MEMBERS))
        self.team_dir = herdr_team.create("w1", "feature-x", "Build it.", flow, MEMBERS, "/unused")
        self.sent = []
        self.notes = []
        notify = mock.patch.object(herdr_team, "notify", lambda *a, **k: self.notes.append(a))
        notify.start()
        self.addCleanup(notify.stop)

    def stage(self, name):
        with herdr_team.state(self.team_dir) as current:
            current["stage"] = name

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

    def test_only_the_role_whose_turn_it_is_reports(self):
        self.stage("build")
        refused = self.call(self.member("planner"), "team.done", {"summary": "plan"})
        self.assertEqual(refused["error"]["code"], "not_your_turn")
        answer = self.call(self.member("coder"), "team.done", {"summary": "built"})
        self.assertIn("runs `check`", answer["result"]["next"])
        events = herdr_team.read_state(self.team_dir)["events"]
        self.assertEqual([(e["kind"], e["role"], e["stage"]) for e in events], [("done", "coder", "build")])
        # Reporting never prompts anyone: the orchestrator moves the work.
        self.assertEqual(self.sent, [])
        self.assertEqual(self.call(self.member("coder"), "team.done", {"summary": "again"})["error"]["code"], "pending")

    def test_findings_only_where_the_stage_allows_them(self):
        self.stage("build")
        refused = self.call(self.member("coder"), "team.findings", {"text": "1. x"})
        self.assertEqual(refused["error"]["code"], "invalid")
        self.stage("review")
        self.assertIn("result", self.call(self.member("reviewer"), "team.findings", {"text": "1. broken"}))

    def test_ask_and_answer_never_change_the_turn(self):
        self.stage("build")
        coder = self.member("coder")
        self.assertEqual(self.call(coder, "team.ask", {"to": "planner", "text": "Which currency?"})["result"],
                         {"asked": "planner"})
        prompt = self.sent[-1]
        self.assertEqual((prompt["method"], prompt["params"]["target"]), ("agent.prompt", "w1:p1"))
        self.assertTrue(prompt["params"]["text"].startswith("[team question from coder] Which currency?"))
        self.call(self.member("planner"), "team.answer", {"to": "coder", "text": "EUR"})
        self.assertTrue(self.sent[-1]["params"]["text"].startswith("[team answer from planner] EUR"))
        current = herdr_team.read_state(self.team_dir)
        self.assertEqual((current["stage"], current["events"]), ("build", []))
        for params in ({"to": "w9:p1", "text": "x"}, {"to": "coder", "text": "x"}, {"to": "planner", "text": " "}):
            self.assertIn("error", self.call(coder, "team.ask", params), params)

    def test_blocked_notifies_and_marks_the_pane(self):
        coder = self.member("coder")
        self.assertEqual(self.call(coder, "team.blocked", {"text": "pytest missing"})["result"], {"blocked": True})
        self.assertEqual(herdr_team.read_state(self.team_dir)["waiting"]["role"], "coder")
        self.assertEqual(self.sent[-1]["method"], "pane.report_metadata")
        self.assertEqual(self.sent[-1]["params"]["tokens"], {"team_waiting": "1"})
        self.assertEqual(len(self.notes), 1)
        self.assertEqual(self.call(coder, "team.blocked", {"text": " "})["error"]["code"], "invalid")

    def test_a_finished_team_takes_no_reports(self):
        self.stage("review")
        with herdr_team.state(self.team_dir) as current:
            current["done"] = True
        self.assertEqual(self.call(self.member("reviewer"), "team.done", {"summary": "x"})["error"]["code"], "finished")

    def test_raw_herdr_methods_stay_blocked(self):
        coder = self.member("coder")
        for method, params in [
            ("pane.run", {"pane_id": "w1:p2", "command": "id"}),
            ("agent.prompt", {"target": "w1:p1", "text": "x"}),
            ("pane.report_agent", {"pane_id": "w1:p1", "state": "idle"}),
            ("team.send", {"to": "reviewer", "text": "x"}),
        ]:
            self.assertEqual(self.call(coder, method, params)["error"]["code"], "forbidden", method)
        self.assertEqual(self.sent, [])
        # Its own pane's report still goes through.
        self.assertIn("result", self.call(coder, "pane.report_agent", {"pane_id": "w1:p2", "state": "idle"}))

    def test_without_a_team_only_reports_pass(self):
        f = herdr_filter.Filter("/unused.sock", "w1:p2")
        answer = json.loads(f.handle(json.dumps({"id": "t", "method": "team.info", "params": {}}).encode()))
        self.assertEqual(answer["error"]["code"], "forbidden")


if __name__ == "__main__":
    unittest.main()
