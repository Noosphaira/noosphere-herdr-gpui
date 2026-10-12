"""herdr-repos against a throwaway home and a fake `gh`."""

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "herdr-repos"

# Answers `gh api` with two pages, and `gh repo clone X DIR` with a new repo
# whose origin points at X.
FAKE_GH = r"""#!/bin/sh
if [ "$1" = api ]; then
  echo '[{"full_name":"me/app","private":true,"description":"An app","pushed_at":"2026-10-01"}]'
  echo '[{"full_name":"org/tool","private":false,"description":null}]'
  exit 0
fi
if [ "$1" = repo ] && [ "$2" = clone ]; then
  git init -q "$4" && git -C "$4" remote add origin "git@github.com:$3.git"
  exit 0
fi
exit 1
"""


class ReposTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.home = root / "home"
        self.repos = root / "repos"
        bin_dir = root / "bin"
        for d in (self.home / ".config/herdr-launch", self.repos, bin_dir):
            d.mkdir(parents=True)
        (bin_dir / "gh").write_text(FAKE_GH)
        (bin_dir / "gh").chmod(0o755)
        (self.home / ".config/herdr-launch/settings.yaml").write_text(f"repos_dir: {self.repos}\n")
        self.env = {**os.environ, "HOME": str(self.home), "PATH": f"{bin_dir}:{os.environ['PATH']}"}

    def run_tool(self, *args):
        result = subprocess.run(["python3", str(SCRIPT), *args], capture_output=True, text=True,
                                env=self.env, check=True)
        return json.loads(result.stdout.strip().splitlines()[-1])

    def checkout(self, folder, origin):
        path = self.repos / folder
        subprocess.run(["git", "init", "-q", str(path)], check=True)
        subprocess.run(["git", "-C", str(path), "remote", "add", "origin", origin], check=True)
        return path

    def test_list_reads_every_page_and_finds_checkouts_by_origin(self):
        local = self.checkout("my-app-folder", "https://github.com/me/app.git")
        answer = self.run_tool("list")
        self.assertTrue(answer["ok"], answer)
        self.assertEqual([r["full_name"] for r in answer["repos"]], ["me/app", "org/tool"])
        self.assertEqual(answer["repos"][0]["local"], str(local))
        self.assertIsNone(answer["repos"][1]["local"])

    def test_ensure_reuses_an_existing_checkout(self):
        local = self.checkout("App", "git@github.com:Me/App.git")
        answer = self.run_tool("ensure", "me/app")
        self.assertEqual((answer["path"], answer["cloned"]), (str(local), False))

    def test_ensure_clones_and_avoids_a_taken_name(self):
        self.checkout("tool", "git@github.com:someone-else/tool.git")
        answer = self.run_tool("ensure", "org/tool")
        self.assertTrue(answer["cloned"], answer)
        self.assertEqual(answer["path"], str(self.repos / "org-tool"))
        # The new clone is found next time instead of cloned again.
        self.assertEqual(self.run_tool("ensure", "org/tool")["cloned"], False)

    def test_problems_are_reported_not_raised(self):
        self.assertFalse(self.run_tool("ensure", "../etc")["ok"])
        (self.home / ".config/herdr-launch/settings.yaml").write_text("repos_dir: /nonexistent/repos\n")
        answer = self.run_tool("list")
        self.assertFalse(answer["ok"])
        self.assertIn("does not exist", answer["error"])


if __name__ == "__main__":
    unittest.main()
