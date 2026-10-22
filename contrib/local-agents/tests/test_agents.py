"""herdr_agents (role commands per agent) and herdr-sandbox's use of it."""

import importlib.machinery
import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
import herdr_agents  # noqa: E402

ROLE = """---
description: Builds things
mode: primary
model: spark/fast
chat_template_kwargs:
  enable_thinking: false
---
You are the coder.
"""


def load_sandbox():
    loader = importlib.machinery.SourceFileLoader("herdr_sandbox", str(HERE / "herdr-sandbox"))
    spec = importlib.util.spec_from_loader("herdr_sandbox", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class RoleCommandTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.agents = Path(self.dir.name)
        (self.agents / "coder.md").write_text(ROLE)
        (self.agents / "reviewer.md").write_text(ROLE.replace("chat_template_kwargs:\n  enable_thinking: false\n", ""))

    def test_opencode_reads_the_role_file_itself(self):
        command, prompt = herdr_agents.role_command("opencode", "coder", agents_dir=self.agents)
        self.assertEqual(command, ["opencode", "--agent", "coder"])
        self.assertIsNone(prompt)

    def test_pi_gets_model_thinking_and_prompt_from_the_role_file(self):
        command, prompt = herdr_agents.role_command("pi", "coder", "/opt/pi/pi", agents_dir=self.agents)
        self.assertEqual(command, [
            "/opt/pi/pi", "--model", "spark/fast", "--thinking", "off",
            "--append-system-prompt", herdr_agents.PROMPT_IN_SANDBOX,
        ])
        self.assertEqual(prompt.strip(), "You are the coder.")

    def test_pi_thinks_unless_the_role_turns_it_off(self):
        command, _ = herdr_agents.role_command("pi", "reviewer", agents_dir=self.agents)
        self.assertEqual(command[command.index("--thinking") + 1], "medium")

    def test_pi_needs_a_model_and_unknown_agents_fail(self):
        (self.agents / "bare.md").write_text("Just a prompt.\n")
        with self.assertRaises(herdr_agents.AgentError):
            herdr_agents.role_command("pi", "bare", agents_dir=self.agents)
        with self.assertRaises(herdr_agents.AgentError):
            herdr_agents.role_command("claude", "coder", agents_dir=self.agents)

    def test_sandbox_dirs_follow_the_executable_name(self):
        self.assertIn(".pi/agent", herdr_agents.sandbox_dirs("/x/pi/1.2.0/pi/pi")["entries_read_only"])
        self.assertIn(".config/opencode", herdr_agents.sandbox_dirs("/usr/bin/opencode")["read_only"])
        self.assertEqual(herdr_agents.sandbox_dirs("/usr/bin/bash"), herdr_agents.SANDBOX_DIRS["opencode"])


class SandboxTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        root = Path(self.dir.name)
        self.home = root / "home"
        install = root / "pi-install"
        install.mkdir()
        self.pi = install / "pi"
        self.pi.write_text("#!/bin/sh\n")
        self.pi.chmod(0o755)
        self.worktree = root / "wt"
        self.worktree.mkdir()
        self.settings = root / "settings.yaml"
        self.settings.write_text(
            "sandbox_env:\n  OPIK_URL: http://127.0.0.1:5173/api\n  OPIK_PROJECT_NAME: teams-{agent}-{branch}\n"
        )
        self.sandbox = load_sandbox()
        patches = [
            mock.patch.object(self.sandbox, "HOME", self.home),
            mock.patch.object(self.sandbox, "SETTINGS", self.settings),
            mock.patch.object(self.sandbox, "ETC_FILES", []),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def bwrap(self, **kwargs):
        grants = {"read_only": [], "read_write": [], "env": [], "path": []}
        return self.sandbox.build_bwrap(
            self.worktree, self.worktree / ".git", grants, [str(self.pi), "--model", "m"], None, **kwargs
        )

    @staticmethod
    def setenv(args):
        return {args[i + 1]: args[i + 2] for i, a in enumerate(args) if a == "--setenv"}

    def test_pi_gets_its_config_install_folder_offline_env_and_settings_env(self):
        (self.home / ".pi/agent/extensions").mkdir(parents=True)
        (self.home / ".pi/agent/settings.json").write_text("{}")
        prompt = Path(self.dir.name) / "coder.md"
        prompt.write_text("You are the coder.\n")
        args = self.bwrap(role="coder", branch="b1", prompt=prompt)
        joined = " ".join(args)
        self.assertIn(f"--tmpfs {self.home}/.pi/agent", joined)
        self.assertIn(f"--ro-bind {self.home}/.pi/agent/settings.json {self.home}/.pi/agent/settings.json", joined)
        self.assertIn(f"--ro-bind {self.home}/.pi/agent/extensions {self.home}/.pi/agent/extensions", joined)
        # sessions is bound read-write after the read-only entries, so it wins.
        sessions = f"{self.home}/.pi/agent/sessions"
        self.assertGreater(joined.index(f"--bind {sessions} {sessions}"), joined.index(f"--tmpfs {self.home}/.pi/agent"))
        self.assertIn(f"--bind {self.home}/.pi/agent/sessions {self.home}/.pi/agent/sessions", joined)
        self.assertNotIn(".config/opencode", joined)
        self.assertIn(f"--ro-bind {self.pi.parent} {self.pi.parent}", joined)
        self.assertIn(f"--ro-bind {prompt} {herdr_agents.PROMPT_IN_SANDBOX}", joined)
        env = self.setenv(args)
        self.assertEqual(env["PI_OFFLINE"], "1")
        self.assertEqual(env["OPIK_PROJECT_NAME"], "teams-pi-b1")
        self.assertEqual(env["HERDR_TEAM_BRANCH"], "b1")
        self.assertEqual(env["HERDR_TEAM_ROLE"], "coder")

    def test_a_team_run_gives_its_id_and_setup_version_to_the_agent(self):
        # README "Runs and traces"
        run = {"HERDR_TEAM_RUN": "20261021T200000Z-abc123", "HERDR_SETUP_VERSION": "0123456789ab"}
        env = self.setenv(self.bwrap(role="coder", branch="b1", run=run))
        self.assertEqual(env["HERDR_TEAM_RUN"], "20261021T200000Z-abc123")
        self.assertEqual(env["HERDR_SETUP_VERSION"], "0123456789ab")
        self.assertNotIn("HERDR_TEAM_RUN", self.setenv(self.bwrap(role="coder", branch="b1")))

    def test_offline_env_wins_over_settings_env(self):
        self.settings.write_text("sandbox_env:\n  PI_OFFLINE: '0'\n")
        self.assertEqual(self.setenv(self.bwrap())["PI_OFFLINE"], "1")


if __name__ == "__main__":
    unittest.main()


def load_launcher():
    loader = importlib.machinery.SourceFileLoader("herdr_launch", str(HERE / "herdr-launch"))
    spec = importlib.util.spec_from_loader("herdr_launch", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class LaunchAgentTest(unittest.TestCase):
    """Which agent herdr-launch picks: the team file, then settings.yaml, then the default."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        root = Path(self.dir.name)
        self.teams = root / "teams"
        self.teams.mkdir()
        agents = root / "agents"
        agents.mkdir()
        (agents / "coder.md").write_text(ROLE)
        self.settings = root / "settings.yaml"
        self.repo = root / "repo"
        self.repo.mkdir()
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        self.launch = load_launcher()
        patches = [
            mock.patch.object(self.launch, "TEAMS_DIR", self.teams),
            mock.patch.object(self.launch, "SETTINGS", self.settings),
            mock.patch.object(self.launch, "AGENTS_DIR", agents),
            mock.patch.object(self.launch.herdr_grants, "load", lambda repo: None),
            mock.patch.object(
                self.launch.herdr_agents, "role_command", lambda agent, role, pi: ([agent, role, pi], None)
            ),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def agent(self, team, settings=""):
        (self.teams / "t.yaml").write_text("roles: [coder]\n" + team)
        self.settings.write_text(settings)
        args = mock.Mock(team="t", repo=str(self.repo), branch="b1", task="do it")
        launcher = self.launch.Launcher(args)
        launcher.validate()
        return launcher.agent, launcher.commands["coder"][0]

    def test_team_file_wins_then_settings_then_opencode(self):
        self.assertEqual(self.agent("agent: opencode\n", "agent: pi\n")[0], "opencode")
        self.assertEqual(self.agent("", "agent: pi\npi_command: /opt/pi\n"), ("pi", ["pi", "coder", "/opt/pi"]))
        self.assertEqual(self.agent("")[0], "opencode")

    def test_the_setup_files_are_the_team_file_role_files_and_launcher_config(self):
        # README "Runs and traces": what the setup version covers.
        self.agent("", "agent: pi\n")
        launcher = self.launch.Launcher(mock.Mock(team="t", repo=str(self.repo), branch="b1", task="do it"))
        launcher.validate()
        config = self.launch.herdr_grants.CONFIG_DIR
        self.assertEqual(
            launcher.setup_files,
            [self.teams / "t.yaml", self.launch.AGENTS_DIR / "coder.md", self.settings,
             config / "global.yaml", config / "repos.yaml"],
        )

    def test_unknown_agent_fails(self):
        with self.assertRaises(self.launch.StepError):
            self.agent("", "agent: claude\n")
