"""herdr-teams against a throwaway home directory."""

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "herdr-teams"

AGENT = """---
description: Builds things
mode: primary
model: spark/fast
color: blue
chat_template_kwargs:
  enable_thinking: false
---
You are the coder.
"""


class TeamsTest(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.TemporaryDirectory()
        self.addCleanup(self.home.cleanup)
        home = Path(self.home.name)
        self.agents = home / ".config/opencode/agents"
        self.teams = home / ".config/herdr-launch/teams"
        self.agents.mkdir(parents=True)
        self.teams.mkdir(parents=True)
        (self.agents / "coder.md").write_text(AGENT)
        (self.agents / "reviewer.md").write_text(AGENT.replace("coder", "reviewer"))
        (home / ".config/opencode/opencode.json").write_text(
            json.dumps({"provider": {"spark": {"models": {"fast": {}, "big": {}}}}})
        )
        (self.teams / "app.yaml").write_text("team: app\nroles: [coder, reviewer]\nreview_rounds: 2\nnote: keep\n")

    def run_tool(self, *args, change=None):
        env = {**os.environ, "HOME": self.home.name}
        result = subprocess.run(
            ["python3", str(SCRIPT), *args], input=json.dumps(change) if change else None,
            capture_output=True, text=True, env=env, check=True,
        )
        return json.loads(result.stdout)

    def state(self):
        return self.run_tool("load")["state"]

    def test_load_reads_everything(self):
        state = self.state()
        self.assertEqual(state["teams"][0]["roles"], ["coder", "reviewer"])
        coder = next(a for a in state["agents"] if a["name"] == "coder")
        self.assertFalse(coder["thinking"])
        self.assertEqual(coder["extra"], {"color": "blue"})
        self.assertEqual(state["models"], ["spark/big", "spark/fast"])

    def test_saving_an_agent_keeps_unknown_keys(self):
        coder = next(a for a in self.state()["agents"] if a["name"] == "coder")
        coder.update(thinking=True, temperature=0.3, steps=40, prompt="New prompt.")
        answer = self.run_tool("apply", change={"op": "save_agent", "agent": coder, "previous": "coder"})
        self.assertTrue(answer["ok"], answer)
        text = (self.agents / "coder.md").read_text()
        self.assertIn("color: blue", text)
        self.assertIn("temperature: 0.3", text)
        self.assertNotIn("enable_thinking", text)
        self.assertTrue(text.endswith("New prompt.\n"))

    def test_renaming_an_agent_updates_its_teams(self):
        coder = next(a for a in self.state()["agents"] if a["name"] == "coder")
        coder["name"] = "builder"
        answer = self.run_tool("apply", change={"op": "save_agent", "agent": coder, "previous": "coder"})
        self.assertTrue(answer["ok"], answer)
        self.assertFalse((self.agents / "coder.md").exists())
        self.assertEqual(answer["state"]["teams"][0]["roles"], ["builder", "reviewer"])
        self.assertIn("note: keep", (self.teams / "app.yaml").read_text())

    def test_invalid_changes_write_nothing(self):
        before = (self.teams / "app.yaml").read_text()
        bad = [
            {"op": "save_team", "previous": "app", "team": {"name": "app", "roles": ["ghost"], "review_rounds": 1}},
            {"op": "save_team", "previous": "app", "team": {"name": "App!", "roles": ["coder"], "review_rounds": 1}},
            {"op": "save_team", "previous": "app", "team": {"name": "app", "roles": [], "review_rounds": 1}},
            {"op": "save_team", "previous": None, "team": {"name": "app", "roles": ["coder"], "review_rounds": 1}},
            {"op": "delete_agent", "name": "coder"},
        ]
        for change in bad:
            answer = self.run_tool("apply", change=change)
            self.assertFalse(answer["ok"], change)
            self.assertTrue(answer["errors"])
        self.assertEqual((self.teams / "app.yaml").read_text(), before)
        self.assertTrue((self.agents / "coder.md").exists())

    def test_access_refuses_sensitive_folders(self):
        access = self.state()["access"]
        access["global"]["read_write"] = [str(Path(self.home.name) / ".ssh")]
        (Path(self.home.name) / ".ssh").mkdir()
        answer = self.run_tool("apply", change={"op": "save_access", "access": access})
        self.assertFalse(answer["ok"])
        access["global"]["read_write"] = []
        access["global"]["read_only"] = [str(self.agents)]
        answer = self.run_tool("apply", change={"op": "save_access", "access": access})
        self.assertTrue(answer["ok"], answer)
        self.assertEqual(answer["state"]["access"]["global"]["read_only"], [str(self.agents)])


if __name__ == "__main__":
    unittest.main()
