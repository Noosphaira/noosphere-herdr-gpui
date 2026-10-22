"""The stages state machine and the orchestrator that carries it out, with
the sandbox and herdr replaced by stand-ins."""

import importlib.machinery
import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
import herdr_flow  # noqa: E402
import herdr_team  # noqa: E402

loader = importlib.machinery.SourceFileLoader("herdr_team_watch", str(HERE / "herdr-team-watch"))
spec = importlib.util.spec_from_loader("herdr_team_watch", loader)
watch = importlib.util.module_from_spec(spec)
loader.exec_module(watch)

ROLES = ["planner", "test-author", "coder", "reviewer"]
TDD = {
    "start": "plan",
    "stages": {
        "plan": {"role": "planner", "next": "red"},
        "red": {"role": "test-author", "check": "red-check", "pass": "green", "fail": "red"},
        "green": {"role": "coder", "check": "check", "pass": "review", "fail": "green"},
        "review": {"role": "reviewer", "next": "finish", "findings": "green"},
    },
    "limits": {"fail": 2, "findings": 1},
}


class ParseTest(unittest.TestCase):
    def test_a_valid_team(self):
        flow = herdr_flow.parse(TDD, ROLES)
        self.assertEqual((flow["start"], flow["limits"]), ("plan", {"fail": 2, "findings": 1}))

    def test_mistakes_are_refused(self):
        bad = [
            {"stages": {"a": {"role": "ghost", "next": "finish"}}},
            {"stages": {"a": {"role": "planner"}}},
            {"stages": {"a": {"role": "planner", "next": "nowhere"}}},
            {"stages": {"a": {"role": "planner", "check": "check", "next": "finish"}}},
            {"stages": {"a": {"role": "planner", "check": "check", "pass": "finish"}}},
            {"stages": {"a": {"role": "planner", "next": "finish", "extra": 1}}},
            {"stages": {"finish": {"role": "planner", "next": "finish"}}},
            {"start": "b", "stages": {"a": {"role": "planner", "next": "finish"}}},
            {"stages": {"a": {"role": "planner", "next": "finish"}}, "limits": {"fail": 99}},
        ]
        for team in bad:
            with self.assertRaises(herdr_flow.FlowError, msg=team):
                herdr_flow.parse(team, ROLES)

    def test_a_flow_without_stages_is_a_chain(self):
        flow = herdr_flow.parse({"flow": ["planner", "coder", "reviewer"], "review_rounds": 2},
                                ["planner", "coder", "reviewer"])
        self.assertEqual(flow["stages"]["planner"], {"role": "planner", "next": "coder"})
        self.assertEqual(flow["stages"]["reviewer"], {"role": "reviewer", "next": "finish", "findings": "coder"})
        self.assertEqual(flow["limits"]["findings"], 2)


class Herdr:
    """Records prompts; every agent is idle."""

    def __init__(self):
        self.prompts = []
        self.calls = []

    def __call__(self, session, *args):
        self.calls.append(args)
        if args[:2] == ("agent", "prompt"):
            self.prompts.append((args[2], args[3]))
        if args[:2] == ("agent", "get"):
            return {"agent": {"agent_status": "idle"}}
        return {}


class RunTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher = mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": self.tmp.name, "XDG_DATA_HOME": self.tmp.name})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.notes = []
        notify = mock.patch.object(herdr_team, "notify", lambda *a: self.notes.append(a))
        notify.start()
        self.addCleanup(notify.stop)
        self.members = {role: f"w1:p{i}" for i, role in enumerate(ROLES, 1)}
        self.team_dir = herdr_team.create("w1", "feature", "Build it.", herdr_flow.parse(TDD, ROLES),
                                          self.members, "/worktree")
        self.herdr = Herdr()
        self.results = {}
        self.commands = []
        self.reports = []
        self.run = watch.Run(self.team_dir, None, sandboxed=self.sandboxed, herdr=self.herdr)

    def sandboxed(self, worktree, command, timeout, report=""):
        self.commands.append(command)
        self.reports.append(report)
        return self.results.get(command, (0, ""))

    def report(self, kind, role, text="x"):
        with herdr_team.state(self.team_dir) as current:
            current["events"].append({"kind": kind, "role": role, "stage": current["stage"], "text": text, "time": 1})
        current = herdr_team.read_state(self.team_dir)
        self.run.handle(current, current["events"][0])
        return herdr_team.read_state(self.team_dir)

    def last_prompt(self):
        pane, text = self.herdr.prompts[-1]
        return next(role for role, p in self.members.items() if p == pane), text

    def test_the_task_goes_to_the_start_stage(self):
        self.run.start()
        role, text = self.last_prompt()
        self.assertEqual(role, "planner")
        self.assertIn("Task:\nBuild it.", text)
        self.assertEqual(herdr_team.read_state(self.team_dir)["stage"], "plan")

    def test_a_whole_run(self):
        self.run.start()
        self.assertEqual(self.report("done", "planner", "brief ready")["stage"], "red")
        self.assertIn("planner finished stage plan: brief ready", self.last_prompt()[1])
        # The check runs in the sandbox and decides; a failure stays with the role.
        self.results["red-check"] = (1, "test_x: NOT RED")
        state = self.report("done", "test-author")
        self.assertEqual((state["stage"], state["counts"]), ("red", {"fail:red": 1}))
        role, text = self.last_prompt()
        self.assertEqual(role, "test-author")
        self.assertIn("`red-check` failed", text)
        self.assertIn("test_x: NOT RED", text)
        self.results["red-check"] = (0, "")
        self.assertEqual(self.report("done", "test-author")["stage"], "green")
        self.assertEqual(self.report("done", "coder", "built it")["stage"], "review")
        self.assertIn("`check` passed", self.last_prompt()[1])
        # The check sees the summary it judges.
        self.assertEqual(self.reports[-1], "built it")
        self.assertEqual(self.report("findings", "reviewer", "1. broken")["stage"], "green")
        self.assertIn("1. broken", self.last_prompt()[1])
        self.report("done", "coder")
        state = self.report("done", "reviewer", "all good")
        self.assertTrue(state["done"])
        self.assertEqual(self.notes[-1][0], "Team finished: feature")
        self.assertEqual(self.commands.count("git status --porcelain"), 6)
        self.assertEqual([e.get("to_stage") for e in state["log"] if "to_stage" in e],
                         ["plan", "red", "red", "green", "review", "green", "review", "finish"])
        self.assertEqual(len(list((Path(self.team_dir) / "checks").glob("*.log"))), 4)

    def test_uncommitted_work_is_sent_back(self):
        self.run.start()
        self.results["git status --porcelain"] = (0, "?? new_file.py\n")
        state = self.report("done", "planner")
        self.assertEqual((state["stage"], state["events"]), ("plan", []))
        role, text = self.last_prompt()
        self.assertEqual(role, "planner")
        self.assertIn("uncommitted changes", text)
        self.assertIn("new_file.py", text)

    def test_a_broken_sandbox_stops_the_team(self):
        self.run.start()
        self.results["git status --porcelain"] = (1, "bwrap: cannot start")
        state = self.report("done", "planner")
        self.assertIn("could not run git status", state["stopped"])
        self.assertEqual(len(self.herdr.prompts), 1)

    def test_limits_stop_the_team(self):
        self.run.start()
        self.report("done", "planner")
        self.results["red-check"] = (1, "")
        self.report("done", "test-author")
        self.report("done", "test-author")
        state = self.report("done", "test-author")
        self.assertIn("failed 3 times", state["stopped"])
        self.assertEqual(self.notes[-1][0], "Team feature stopped")

    def test_findings_have_a_limit(self):
        self.run.start()
        with herdr_team.state(self.team_dir) as current:
            current["stage"] = "review"
        self.assertEqual(self.report("findings", "reviewer")["stage"], "green")
        with herdr_team.state(self.team_dir) as current:
            current["stage"] = "review"
        self.assertIn("findings were sent 2 times", self.report("findings", "reviewer")["stopped"])

    def test_a_report_from_an_old_stage_is_dropped(self):
        self.run.start()
        with herdr_team.state(self.team_dir) as current:
            current["events"].append({"kind": "done", "role": "coder", "stage": "green", "text": "x", "time": 1})
        current = herdr_team.read_state(self.team_dir)
        self.run.handle(current, current["events"][0])
        state = herdr_team.read_state(self.team_dir)
        self.assertEqual((state["stage"], state["events"]), ("plan", []))


if __name__ == "__main__":
    unittest.main()
