"""herdr_netfilter: refuse to run agents unless the host's filter matches their slice."""

import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
import herdr_netfilter  # noqa: E402


class ProblemTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        root = Path(self.dir.name)
        self.cgroup = root / "herdr-agents.slice"
        self.cgroup.mkdir()
        self.id_file = root / "cgroup-id"

    def problem(self):
        return herdr_netfilter.problem(self.id_file, self.cgroup)

    def test_active_when_loaded_id_matches_the_slice(self):
        self.id_file.write_text(f"{self.cgroup.stat().st_ino}\n")
        self.assertIsNone(self.problem())

    def test_refuses_without_a_loaded_filter(self):
        self.assertIn("no network filter is loaded", self.problem())

    def test_refuses_an_unreadable_id(self):
        self.id_file.write_text("garbage\n")
        self.assertIn("no network filter is loaded", self.problem())

    def test_refuses_when_the_slice_was_recreated(self):
        self.id_file.write_text(f"{self.cgroup.stat().st_ino + 1}\n")
        self.assertIn("old herdr-agents.slice", self.problem())

    def test_refuses_without_the_slice(self):
        self.cgroup.rmdir()
        self.assertIn("does not exist", self.problem())


class SettingsTest(unittest.TestCase):
    def test_required_by_default(self):
        self.assertEqual(herdr_netfilter.mode({}), "required")
        self.assertEqual(herdr_netfilter.mode(None), "required")

    def test_off(self):
        self.assertEqual(herdr_netfilter.mode({"network_filter": "off"}), "off")

    def test_rejects_other_values(self):
        with self.assertRaises(ValueError):
            herdr_netfilter.mode({"network_filter": False})


class WrapTest(unittest.TestCase):
    def test_runs_the_command_in_a_scope_in_the_slice(self):
        wrapped = herdr_netfilter.wrap(["bwrap", "--", "pi"])
        self.assertEqual(wrapped[:2], ["systemd-run", "--user"])
        self.assertIn("--scope", wrapped)
        self.assertIn("--slice=herdr-agents.slice", wrapped)
        self.assertEqual(wrapped[wrapped.index("--") + 1:], ["bwrap", "--", "pi"])

    def test_slice_path(self):
        self.assertEqual(
            herdr_netfilter.slice_dir(1000),
            Path("/sys/fs/cgroup/user.slice/user-1000.slice/user@1000.service/herdr.slice/herdr-agents.slice"),
        )


if __name__ == "__main__":
    unittest.main()
