"""Team runs (README "Runs and traces"): every launch gets a run id and a
setup version, a lasting run record says how it went, and every agent of the
run gets both in its environment so its traces can be found by run."""

import hashlib
import importlib.machinery
import importlib.util
import json
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


def load(name, file):
    loader = importlib.machinery.SourceFileLoader(name, str(HERE / file))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


watch = load("herdr_team_watch", "herdr-team-watch")
sandbox = load("herdr_sandbox", "herdr-sandbox")

ROLES = ["planner", "coder"]
FLOW = herdr_flow.parse({"flow": ROLES}, ROLES)
MEMBERS = {"planner": "w1:p1", "coder": "w1:p2"}


def expected_version(files):
    """README: sha256 over each file's path and content hash, sorted by path,
    first 12 hex digits."""
    h = hashlib.sha256()
    for path in sorted(files):
        h.update(f"{path}\0{files[path] or '<missing>'}\0".encode())
    return h.hexdigest()[:12]


class RunsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        env = mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": str(root / "run"), "XDG_DATA_HOME": str(root / "data")})
        env.start()
        self.addCleanup(env.stop)
        (root / "run").mkdir()
        notify = mock.patch.object(herdr_team, "notify", lambda *a: None)
        notify.start()
        self.addCleanup(notify.stop)
        self.setup_dir = root / "setup"
        self.setup_dir.mkdir()
        self.role = self.setup_dir / "coder.md"
        self.role.write_text("You are the coder.\n")
        self.team_file = self.setup_dir / "team.yaml"
        self.team_file.write_text("flow: [planner, coder]\n")
        self.runs = root / "data" / "herdr-launch" / "runs"

    def create(self, setup_files=None):
        files = setup_files if setup_files is not None else [self.role, self.team_file]
        return herdr_team.create("w1", "feature", "Build it.", FLOW, MEMBERS, "/worktree", setup_files=files,
                                 team_name="pair-team")

    def record(self, team_dir):
        run_id = herdr_team.load(team_dir)["run_id"]
        return json.loads((self.runs / f"{run_id}.json").read_text())

    def test_each_launch_gets_its_own_run_id(self):
        a = herdr_team.load(self.create())["run_id"]
        b = herdr_team.load(self.create())["run_id"]
        self.assertNotEqual(a, b)
        # Usable in file names, Opik tags and environment values.
        for run_id in (a, b):
            self.assertRegex(run_id, r"^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{6}$")

    def test_the_setup_version_covers_every_setup_file(self):
        team = herdr_team.load(self.create())
        files = {
            str(self.role): hashlib.sha256(b"You are the coder.\n").hexdigest(),
            str(self.team_file): hashlib.sha256(b"flow: [planner, coder]\n").hexdigest(),
        }
        self.assertEqual(team["setup"], {"version": expected_version(files), "files": files})

    def test_same_files_same_version_any_change_a_new_one(self):
        first = herdr_team.load(self.create())["setup"]["version"]
        self.assertEqual(herdr_team.load(self.create())["setup"]["version"], first)
        self.role.write_text("You are the coder. Run the tests.\n")
        self.assertNotEqual(herdr_team.load(self.create())["setup"]["version"], first)

    def test_a_missing_setup_file_is_recorded_as_missing(self):
        gone = self.setup_dir / "gone.md"
        setup = herdr_team.load(self.create([self.role, gone]))["setup"]
        self.assertIsNone(setup["files"][str(gone)])
        self.assertEqual(setup["version"], expected_version(setup["files"]))

    def test_no_setup_files_still_gives_a_version(self):
        setup = herdr_team.load(self.create([]))["setup"]
        self.assertEqual(setup, {"version": expected_version({}), "files": {}})

    def test_the_launch_writes_a_lasting_run_record(self):
        team_dir = self.create()
        team = herdr_team.load(team_dir)
        record = self.record(team_dir)
        self.assertEqual(record["run_id"], team["run_id"])
        self.assertEqual(record["setup"], team["setup"])
        self.assertEqual(
            {k: record[k] for k in ("team", "workspace_id", "branch", "task", "worktree", "roles", "outcome")},
            {"team": "pair-team", "workspace_id": "w1", "branch": "feature", "task": "Build it.",
             "worktree": "/worktree", "roles": ROLES, "outcome": None},
        )
        self.assertRegex(record["started_at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        # Not under $XDG_RUNTIME_DIR, which is gone after a reboot.
        self.assertFalse(str(self.runs).startswith(os.environ["XDG_RUNTIME_DIR"]))

    def orchestrator(self, team_dir):
        class Herdr:
            def __call__(self, session, *args):
                return {}
        return watch.Run(team_dir, None, sandboxed=lambda *a: (0, ""), herdr=Herdr())

    def report(self, run, team_dir, kind, role, text):
        with herdr_team.state(team_dir) as current:
            current["events"].append({"kind": kind, "role": role, "stage": current["stage"], "text": text, "time": 1})
        current = herdr_team.read_state(team_dir)
        run.handle(current, current["events"][0])

    def test_a_finished_run_records_its_outcome_and_summary(self):
        team_dir = self.create()
        run = self.orchestrator(team_dir)
        run.start()
        self.report(run, team_dir, "done", "planner", "planned")
        self.report(run, team_dir, "done", "coder", "built and tested")
        record = self.record(team_dir)
        self.assertEqual(record["outcome"], {"result": "finished", "summary": "built and tested", "ended_at": mock.ANY})
        self.assertRegex(record["outcome"]["ended_at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        self.assertEqual([e.get("to_stage") for e in record["log"]][-1], herdr_flow.FINISH)

    def test_a_stopped_run_records_why(self):
        team_dir = self.create()
        run = self.orchestrator(team_dir)
        run.start()
        run.apply({"stop": "limit reached: 3 failed checks"})
        record = self.record(team_dir)
        self.assertEqual(record["outcome"]["result"], "stopped")
        self.assertEqual(record["outcome"]["reason"], "limit reached: 3 failed checks")

    def test_a_failed_check_keeps_the_tail_of_its_output_in_the_record(self):
        flow = herdr_flow.parse({"start": "build", "stages": {
            "build": {"role": "coder", "check": "check", "pass": "finish", "fail": "build"}}}, ROLES)
        team_dir = herdr_team.create("w1", "feature", "Build it.", flow, MEMBERS, "/worktree",
                                     setup_files=[self.role], team_name="pair-team")
        results = iter([(1, "noise\n" * 1000 + "AssertionError: 2 != 3"), (0, "all passed")])

        class Herdr:
            def __call__(self, session, *args):
                return {}
        run = watch.Run(team_dir, None, sandboxed=lambda *a: next(results) if a[1] == "check" else (0, ""),
                        herdr=Herdr())
        run.start()
        self.report(run, team_dir, "done", "coder", "built")
        self.report(run, team_dir, "done", "coder", "fixed")
        checks = [e for e in self.record(team_dir)["log"] if "check" in e]
        self.assertEqual([c["exit"] for c in checks], [1, 0])
        self.assertTrue(checks[0]["output_tail"].endswith("AssertionError: 2 != 3"))
        self.assertLessEqual(len(checks[0]["output_tail"]), watch.OUTPUT_TAIL)
        self.assertNotIn("output_tail", checks[1])

    def test_a_record_that_cannot_be_written_never_stops_the_team(self):
        team_dir = self.create()
        run = self.orchestrator(team_dir)
        run.start()
        (self.runs / f"{herdr_team.load(team_dir)['run_id']}.json").unlink()
        self.runs.chmod(0o500)
        self.addCleanup(self.runs.chmod, 0o700)
        self.report(run, team_dir, "done", "planner", "planned")
        self.report(run, team_dir, "done", "coder", "built")
        self.assertTrue(herdr_team.read_state(team_dir)["done"])


class SandboxEnvTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        env = mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": str(root), "XDG_DATA_HOME": str(root / "data")})
        env.start()
        self.addCleanup(env.stop)
        self.settings = root / "settings.yaml"
        self.settings.write_text("sandbox_env:\n  OPIK_PROJECT_NAME: agent-teams\n  TAG: run-{run}\n")
        patch = mock.patch.object(sandbox, "SETTINGS", self.settings)
        patch.start()
        self.addCleanup(patch.stop)
        role = root / "coder.md"
        role.write_text("x")
        self.team_dir = herdr_team.create("w1", "feature", "Build it.", FLOW, MEMBERS, "/worktree", setup_files=[role])
        self.team = herdr_team.load(self.team_dir)

    def test_agents_get_the_run_id_and_setup_version(self):
        env = sandbox.run_env(self.team)
        self.assertEqual(env, {"HERDR_TEAM_RUN": self.team["run_id"], "HERDR_SETUP_VERSION": self.team["setup"]["version"]})

    def test_settings_env_can_name_the_run(self):
        env = sandbox.sandbox_env("pi", "coder", "feature", self.team["run_id"])
        self.assertEqual(env["TAG"], f"run-{self.team['run_id']}")

    def test_a_team_dir_from_before_run_ids_gives_no_run_env(self):
        self.assertEqual(sandbox.run_env({"branch": "feature"}), {})


if __name__ == "__main__":
    unittest.main()
