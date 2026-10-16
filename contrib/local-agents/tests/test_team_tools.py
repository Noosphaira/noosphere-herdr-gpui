"""team-tools: the deterministic checks team roles run, against throwaway repos."""

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
spec = importlib.util.spec_from_file_location("team_tools", HERE / "team-tools" / "team_tools.py")
tt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tt)

BRIEF = """# Brief

## INTENT
Invoices show a currency.

## DONE WHEN
- AC-1: an invoice total prints with its currency code
- AC-2: a missing currency is rejected

## GUARDRAILS
No new dependencies.

## OWNED PATHS
- src/
- tests/

## TEST COMMAND
python3 -m pytest -q

## CONTEXT POINTERS
- src/invoice.py
"""


class RepoCase(unittest.TestCase):
    """A git repo in a temp dir, with main at one commit and a work branch."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.repo = Path(self.dir.name)
        self.old_cwd = os.getcwd()
        os.chdir(self.repo)
        self.addCleanup(os.chdir, self.old_cwd)
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        self.env.update(GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t",
                        GIT_COMMITTER_EMAIL="t@t", GIT_CONFIG_GLOBAL="/dev/null")
        os.environ.pop("TEAM_BASE", None)
        self.git("init", "-q", "-b", "main")
        self.write("pyproject.toml", "[project]\nname='x'\n")
        self.write("src/invoice.py", "def total(x):\n    return x\n")
        self.write("tests/test_invoice.py", "def test_old():\n    assert True\n")
        self.commit("initial")
        self.git("switch", "-q", "-c", "work")

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.repo, env=self.env, check=True,
                              capture_output=True, text=True).stdout

    def write(self, path, text):
        target = self.repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)

    def commit(self, message):
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)

    def info(self):
        return tt.facts(self.repo)


class GlobTest(unittest.TestCase):
    def test_patterns(self):
        cases = [
            ("src/a.test.ts", ["**/*.test.*"], True),
            ("a.test.ts", ["**/*.test.*"], True),
            ("src/a.ts", ["**/*.test.*"], False),
            ("tests/x/y.py", ["tests/**"], True),
            ("tests/x/y.py", ["tests/"], True),
            ("tests/x/y.py", ["tests"], True),
            ("testsuite/y.py", ["tests"], False),
            (".github/w.yml", [".github/"], True),
            ("src/billing/a.ts", ["src/billing/*"], True),
            ("src/billing/x/a.ts", ["src/billing/*"], False),
        ]
        for path, patterns, expected in cases:
            with self.subTest(path=path, patterns=patterns):
                self.assertEqual(tt.matches(path, patterns), expected)


class JunitTest(unittest.TestCase):
    def test_vitest_failure_kinds(self):
        # Captured from vitest 4 (see fixtures/vitest-junit.xml): an assertion,
        # two thrown TypeErrors, a pass, and a file that does not load.
        cases = {c["name"]: c for c in tt.parse_junit(FIXTURES / "vitest-junit.xml")}
        self.assertEqual(cases["assert fails AC-1"]["kind"], "assertion")
        self.assertEqual(cases["assert fails AC-1"]["location"], "a.test.ts:3:47")
        self.assertEqual(cases["type error"]["kind"], "error")
        self.assertEqual(cases["not a function"]["kind"], "error")
        self.assertEqual(cases["passes"]["status"], "passed")
        self.assertEqual(cases["b.test.ts"]["kind"], "load")

    def test_pytest_failure_and_error(self):
        with tempfile.NamedTemporaryFile("w", suffix=".xml", delete=False) as handle:
            handle.write("""<testsuites><testsuite name="pytest">
<testcase classname="tests.test_a" name="test_assert"><failure message="assert 2 == 3">tests/test_a.py:4: AssertionError</failure></testcase>
<testcase classname="tests.test_a" name="test_raises"><failure message="ZeroDivisionError: division by zero">tests/test_a.py:7: ZeroDivisionError</failure></testcase>
<testcase classname="tests.test_a" name="test_setup"><error message="failed on setup with &quot;fixture 'db' not found&quot;">E fixture 'db' not found</error></testcase>
<testcase classname="tests.test_a" name="test_skip"><skipped message="later"/></testcase>
</testsuite></testsuites>""")
        self.addCleanup(os.unlink, handle.name)
        cases = {c["name"]: c for c in tt.parse_junit(Path(handle.name))}
        self.assertEqual(cases["test_assert"]["kind"], "assertion")
        self.assertEqual(cases["test_raises"]["kind"], "error")
        self.assertEqual(cases["test_setup"]["kind"], "error")
        self.assertEqual(cases["test_skip"]["status"], "skipped")


class BriefTest(RepoCase):
    def test_good_brief(self):
        self.assertEqual(tt.lint_brief(self.repo, BRIEF), ([], []))
        self.assertEqual(tt.brief_acs(BRIEF), [1, 2])
        self.assertEqual(tt.owned_paths(BRIEF), ["src/", "tests/"])

    def test_problems(self):
        brief = BRIEF.replace("## GUARDRAILS\nNo new dependencies.\n", "")
        brief = brief.replace("- AC-2:", "- AC-3:").replace("- src/\n", "- nowhere/deep/x/\n")
        brief = brief.replace("python3 -m pytest", "no-such-runner")
        problems, _ = tt.lint_brief(self.repo, brief)
        text = "\n".join(problems)
        self.assertIn("GUARDRAILS", text)
        self.assertIn("numbered AC-1..AC-2", text)
        self.assertIn("nowhere/deep/x/", text)
        self.assertIn("no-such-runner", text)

    def test_too_many_acs(self):
        many = "\n".join(f"- AC-{i}: thing {i}" for i in range(1, 10))
        brief = BRIEF.replace("- AC-1: an invoice total prints with its currency code\n- AC-2: a missing currency is rejected", many)
        problems, _ = tt.lint_brief(self.repo, brief)
        self.assertTrue(any("at most 8" in p for p in problems))

    def test_open_questions(self):
        _, questions = tt.lint_brief(self.repo, BRIEF + "\n## OPEN QUESTIONS\n- Which currencies?\n")
        self.assertEqual(questions, ["- Which currencies?"])
        _, none = tt.lint_brief(self.repo, BRIEF + "\n## OPEN QUESTIONS\n- none\n")
        self.assertEqual(none, [])


class TraceTest(RepoCase):
    def test_coverage_and_citations(self):
        self.write("plan/brief.md", BRIEF)
        self.write("tests/test_currency.py", "\n".join([
            "# AC-1: prints with its code",
            "def test_prints_code():",
            "    assert False",
            "",
            "def test_uncited():",
            "    assert False",
            "",
            "def test_ac9():  # AC-9",
            "    assert False",
        ]) + "\n")
        self.commit("red: currency")
        traced = tt.trace(self.repo, self.info())
        self.assertEqual(traced["uncovered"], ["AC-2"])
        self.assertEqual(len(traced["covered"]["AC-1"]), 1)
        self.assertTrue(traced["uncited"][0].endswith("test_uncited"))
        self.assertIn("AC-9", traced["unknown"][0])

    def test_js_declarations(self):
        self.write("a.test.ts", "\n".join([
            "describe('x', () => {",
            "  // AC-2: rejects",
            "  it('rejects a missing currency', () => {})",
            "  test(\"AC-1 prints\", () => {})",
            "  it.each([1, 2])('handles %s', () => {})",
            "})",
        ]))
        found = tt.test_declarations(self.repo / "a.test.ts")
        self.assertEqual([(name, acs) for _, name, acs in found],
                         [("rejects a missing currency", [2]), ("AC-1 prints", [1]), ("handles %s", [])])


class GamingTest(RepoCase):
    def red(self):
        self.write("tests/test_currency.py", "# AC-1\ndef test_code():\n    assert total(1) == 'EUR 1'\n")
        self.commit("red: currency code")

    def test_clean_green_passes(self):
        self.red()
        self.write("src/invoice.py", "def total(x):\n    return f'EUR {x}'\n")
        self.commit("green: currency code")
        violations, _ = tt.gaming(self.repo, self.info())
        self.assertEqual(violations, [])

    def test_test_change_after_red(self):
        self.red()
        self.write("tests/test_currency.py", "def test_code():\n    assert True\n")
        self.write("src/invoice.py", "def total(x):\n    return x\n# changed\n")
        self.commit("green: currency code")
        violations, _ = tt.gaming(self.repo, self.info())
        self.assertTrue(any("changes tests after RED" in v for v in violations), violations)

    def test_uncommitted_test_change_after_green(self):
        self.red()
        self.write("src/invoice.py", "def total(x):\n    return f'EUR {x}'\n")
        self.commit("green: currency code")
        self.write("tests/test_currency.py", "def test_code():\n    assert True\n")
        violations, _ = tt.gaming(self.repo, self.info())
        self.assertTrue(any("uncommitted test changes" in v for v in violations), violations)

    def test_skip_marker_and_removed_test(self):
        self.write("tests/test_invoice.py", "import pytest\n\n@pytest.mark.skip\ndef test_other():\n    assert True\n")
        self.commit("refactor: tidy")
        violations, _ = tt.gaming(self.repo, self.info())
        text = "\n".join(violations)
        self.assertIn("skip marker", text)
        self.assertIn('removes test "test_old"', text)

    def test_deleted_test_file(self):
        (self.repo / "tests/test_invoice.py").unlink()
        self.commit("chore: remove")
        violations, _ = tt.gaming(self.repo, self.info())
        self.assertTrue(any("test file deleted" in v for v in violations), violations)

    def test_import_lines_are_not_expected_values(self):
        self.write("tests/a.test.ts", "import { expect, it } from 'vitest'\nit('x', () => expect(f()).toBe(1))\n")
        self.write("src/b.ts", "// uses vitest\nexport const runner = 'vitest'\n")
        self.commit("green: x")
        _, hints = tt.gaming(self.repo, self.info())
        self.assertEqual(hints, [])

    def test_hardcoded_value_is_a_hint(self):
        self.write("tests/test_currency.py", "def test_code():\n    assert total(42) == 'EUR 42.00 net'\n")
        self.commit("red: format")
        self.write("src/invoice.py", "def total(x):\n    if x == 42:\n        return 'EUR 42.00 net'\n    return x\n")
        self.commit("green: format")
        violations, hints = tt.gaming(self.repo, self.info())
        self.assertEqual(violations, [])
        self.assertTrue(any("EUR 42.00 net" in h for h in hints), hints)


class KnownFailuresTest(unittest.TestCase):
    def case(self, name, status="failed"):
        return {"file": "a.test.ts", "name": name, "status": status, "kind": "assertion",
                "message": "m", "location": ""}

    def test_only_new_failures_block(self):
        known = {"a.test.ts > needs db"}
        self.assertTrue(tt.tests_ok(1, [self.case("needs db"), self.case("ok", "passed")], known))
        self.assertFalse(tt.tests_ok(1, [self.case("needs db"), self.case("new")], known))
        self.assertTrue(tt.tests_ok(0, [self.case("ok", "passed")], set()))
        # A non-zero exit with no failing test (a crash, a type error) is red.
        self.assertFalse(tt.tests_ok(1, [self.case("ok", "passed")], known))
        self.assertFalse(tt.tests_ok(1, None, known))

    def test_summary_names_only_new_failures(self):
        log = Path(tempfile.mkstemp()[1])
        self.addCleanup(log.unlink)
        text = tt.summarize(1, [self.case("needs db"), self.case("new")], log, {"a.test.ts > needs db"})
        self.assertIn("1 of the failures were already failing", text)
        self.assertIn("FAIL a.test.ts > new", text)
        self.assertNotIn("FAIL a.test.ts > needs db", text)


class BaseTest(RepoCase):
    def test_base_is_saved_and_ignored(self):
        start = self.git("rev-parse", "main").strip()
        self.write("x.txt", "x")
        self.commit("work")
        self.assertEqual(tt.base(self.repo), start)
        self.git("switch", "-q", "main")
        self.write("y.txt", "y")
        self.commit("main moves on")
        self.git("switch", "-q", "work")
        self.assertEqual(tt.base(self.repo), start)
        self.assertNotIn(".team", self.git("status", "--porcelain"))


class FactsTest(RepoCase):
    def test_node_vitest(self):
        (self.repo / "pyproject.toml").unlink()
        self.write("package.json", json.dumps({"scripts": {"test": "vitest run", "typecheck": "tsc", "test:mutation": "stryker run"},
                                               "devDependencies": {"vitest": "4"}}))
        self.write("package-lock.json", "{}")
        info = self.info()
        self.assertEqual((info["stack"], info["runner"], info["setup"]), ("node", "vitest", "npm ci"))
        self.assertEqual(info["commands"]["mutation"], "npm run test:mutation")
        command = tt.test_command(info, ["a.test.ts"], Path("/x.xml"))
        self.assertEqual(command[:4], ["npx", "vitest", "run", "a.test.ts"])
        self.assertIn("--outputFile.junit=/x.xml", command)

    def test_python(self):
        info = self.info()
        self.assertEqual((info["stack"], info["runner"]), ("python", "pytest"))


if __name__ == "__main__":
    unittest.main()
