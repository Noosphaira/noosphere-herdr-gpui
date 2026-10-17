"""team-tools: the deterministic checks team roles run, against throwaway repos."""

import contextlib
import importlib.util
import io
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

## FEATURES
- features/currency.feature

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

FEATURE = """Feature: Invoice currency
  Invoices name the currency their total is in.

  @AC-1
  Scenario: The total prints with its currency code
    Given an invoice in EUR with a total of 12.50
    When the invoice is printed
    Then the total reads "EUR 12.50"

  @AC-2
  Scenario Outline: A missing currency is rejected
    Given an invoice with currency "<currency>"
    When the invoice is printed
    Then printing fails with "missing currency"

    Examples:
      | currency |
      |          |
      | none     |
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
    def setUp(self):
        super().setUp()
        self.write("features/currency.feature", FEATURE)

    def test_good_brief(self):
        self.assertEqual(tt.lint_brief(self.repo, BRIEF), ([], []))
        scenarios, _ = tt.ticket_scenarios(self.repo, BRIEF)
        self.assertEqual([s["acs"] for s in scenarios], [[1], [2]])
        self.assertEqual(tt.owned_paths(BRIEF), ["src/", "tests/"])

    def test_problems(self):
        brief = BRIEF.replace("## GUARDRAILS\nNo new dependencies.\n", "")
        brief = brief.replace("- src/\n", "- nowhere/deep/x/\n")
        brief = brief.replace("python3 -m pytest", "no-such-runner")
        self.write("features/currency.feature", FEATURE.replace("@AC-2", "@AC-3"))
        problems, _ = tt.lint_brief(self.repo, brief)
        text = "\n".join(problems)
        self.assertIn("GUARDRAILS", text)
        self.assertIn("numbered AC-1..AC-2", text)
        self.assertIn("nowhere/deep/x/", text)
        self.assertIn("no-such-runner", text)

    def test_done_when_is_gone(self):
        problems, _ = tt.lint_brief(self.repo, BRIEF + "\n## DONE WHEN\n- AC-1: x\n")
        self.assertTrue(any("replaced by FEATURES" in p for p in problems), problems)

    def test_scenario_problems(self):
        self.write("features/currency.feature", "\n".join([
            "Feature: Invoice currency",
            "  @AC-1 @AC-2",
            "  Scenario: Two tags",
            "    Given a",
            "    When b",
            "    Then c",
            "  Scenario: No tag and no outcome",
            "    Given a",
            "    When b",
            "  @AC-3",
            "  Scenario Outline: No examples",
            "    When <x>",
            "    Then y",
            "  @AC-3",
            "  Scenario: Two tags",
            "    Given a",
            "    When b",
            "    And c",
            "    Then d",
        ]) + "\n")
        text = "\n".join(tt.lint_brief(self.repo, BRIEF)[0])
        self.assertIn("needs exactly one @AC-n tag, has 2", text)
        self.assertIn("needs exactly one @AC-n tag, has 0", text)
        self.assertIn("no Then step", text)
        self.assertIn("Scenario Outline without Examples rows", text)
        self.assertIn("AC-3 tags 2 scenarios", text)
        self.assertIn('"Two tags" is used 2 times', text)

    def test_missing_feature_file(self):
        (self.repo / "features/currency.feature").unlink()
        text = "\n".join(tt.lint_brief(self.repo, BRIEF)[0])
        self.assertIn("feature file features/currency.feature not found", text)

    def test_only_new_or_changed_scenarios_count(self):
        self.commit("plan: currency")
        self.git("branch", "-f", "main", "HEAD")
        added = FEATURE + "\n  @AC-1\n  Scenario: Totals round to cents\n    When the total is 0.125\n    Then it prints 0.13\n"
        self.write("features/currency.feature", added.replace("12.50", "13.00"))
        scenarios, _ = tt.ticket_scenarios(self.repo, BRIEF)
        self.assertEqual(sorted(s["name"] for s in scenarios),
                         ["The total prints with its currency code", "Totals round to cents"])

    def test_too_many_acs(self):
        many = "Feature: Many\n" + "".join(f"  @AC-{i}\n  Scenario: thing {i}\n    When {i}\n    Then {i}\n" for i in range(1, 10))
        self.write("features/currency.feature", many)
        problems, _ = tt.lint_brief(self.repo, BRIEF)
        self.assertTrue(any("at most 8" in p for p in problems), problems)

    def test_open_questions(self):
        _, questions = tt.lint_brief(self.repo, BRIEF + "\n## OPEN QUESTIONS\n- Which currencies?\n")
        self.assertEqual(questions, ["- Which currencies?"])
        _, none = tt.lint_brief(self.repo, BRIEF + "\n## OPEN QUESTIONS\n- none\n")
        self.assertEqual(none, [])


class GherkinTest(unittest.TestCase):
    def test_parse(self):
        parsed = tt.parse_feature(FEATURE + '\n  Background:\n    Given ignored\n  Scenario: Doc\n    Given text\n      """\n      Then not a step\n      """\n    Then done  # not a comment\n')
        self.assertEqual(parsed["name"], "Invoice currency")
        first, outline, doc = parsed["scenarios"]
        self.assertEqual((first["tags"], first["line"]), (["@AC-1"], 5))
        self.assertEqual(first["steps"][2], ("Then", 'the total reads "EUR 12.50"'))
        self.assertEqual((outline["outline"], outline["examples"]), (True, 2))
        self.assertEqual([k for k, _ in doc["steps"]], ["Given", "Then"])
        self.assertEqual(tt.step_kinds([("Given", "a"), ("And", "b"), ("When", "c"), ("But", "d")]),
                         ["Given", "Given", "When", "When"])

    def test_python_name(self):
        # pytest-bdd's names, from pytest_bdd.scenario.make_python_name.
        self.assertEqual(tt.python_name("Totals are rounded to cents"), "totals_are_rounded_to_cents")
        self.assertEqual(tt.python_name("2 lines, 1 total!"), "lines_1_total")

    def index(self):
        return [{**s, "file": "features/totals.feature", "feature": "Invoice totals"}
                for s in tt.parse_feature("\n".join([
                    "Feature: Invoice totals",
                    "  @AC-1", "  Scenario: Totals are rounded to cents",
                    "    Given an invoice with lines 0.1 and 0.2", "    When the total is computed", "    Then the total is 0.30",
                    "  @AC-2", "  Scenario: An empty invoice is rejected",
                    "    Given an invoice with no lines", "    When the total is computed", '    Then it fails with "empty invoice"',
                ]))["scenarios"]]

    def test_vitest_cucumber_steps_group_per_scenario(self):
        # Captured from @amiceli/vitest-cucumber 8 with a stub returning 0.
        cases = tt.parse_junit(FIXTURES / "vitest-cucumber-junit.xml")
        groups, plain = tt.group_cases(cases, self.index(), {"bdd": "vitest-cucumber"})
        self.assertEqual(plain, [])
        rounded = groups[("features/totals.feature", "Totals are rounded to cents")]
        self.assertEqual(rounded["status"], "failed")
        self.assertEqual([step for _, step in rounded["cases"]][0], "Given an invoice with lines 0.1 and 0.2")

    def test_vitest_cucumber_scenario_without_code(self):
        cases = tt.parse_junit(FIXTURES / "vitest-cucumber-not-called-junit.xml")
        groups, _ = tt.group_cases(cases, self.index(), {"bdd": "vitest-cucumber"})
        self.assertEqual(groups[("features/totals.feature", "An empty invoice is rejected")]["status"], "broken")

    def test_pytest_bdd(self):
        # Captured from pytest-bdd 9: one assertion failure, one step with no code.
        cases = tt.parse_junit(FIXTURES / "pytest-bdd-junit.xml")
        groups, plain = tt.group_cases(cases, self.index(), {"bdd": "pytest-bdd"})
        self.assertEqual(plain, [])
        self.assertEqual(groups[("features/totals.feature", "Totals are rounded to cents")]["status"], "failed")
        self.assertEqual(groups[("features/totals.feature", "An empty invoice is rejected")]["status"], "broken")
        # Without pytest-bdd in the repo, test_* names are plain tests.
        self.assertEqual(len(tt.group_cases(cases, self.index(), {"bdd": None})[1]), 2)


class TraceTest(RepoCase):
    def setUp(self):
        super().setUp()
        self.write("features/currency.feature", FEATURE)
        self.write("plan/brief.md", BRIEF)

    def test_coverage_and_citations(self):
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
        self.assertEqual(traced["unbound"], [])

    def test_bound_scenarios(self):
        self.write("pyproject.toml", "[project]\nname='x'\ndependencies=['pytest-bdd']\n")
        self.write("tests/test_currency_steps.py", 'from pytest_bdd import scenario\n'
                   '@scenario("../features/currency.feature", "The total prints with its currency code")\n'
                   'def test_x():\n    pass\n')
        self.commit("red: currency")
        traced = tt.trace(self.repo, self.info())
        self.assertEqual(traced["uncovered"], ["AC-2"])
        self.assertIn("bound in tests/test_currency_steps.py", traced["covered"]["AC-1"][0])
        self.assertIn("A missing currency is rejected", traced["unbound"][0])
        self.write("tests/test_currency_steps.py", 'from pytest_bdd import scenarios\nscenarios("../features/currency.feature")\n')
        traced = tt.trace(self.repo, self.info())
        self.assertEqual((traced["uncovered"], traced["unbound"]), ([], []))

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

    def test_feature_change_outside_plan(self):
        self.write("features/currency.feature", FEATURE)
        self.commit("plan: currency")
        self.red()
        self.write("features/currency.feature", FEATURE.replace("12.50", "0"))
        self.commit("plan: planner updates a scenario after RED")
        self.assertEqual(tt.gaming(self.repo, self.info())[0], [])
        self.write("features/currency.feature", FEATURE.replace("12.50", "1"))
        self.assertTrue(any("uncommitted feature file" in v for v in tt.gaming(self.repo, self.info())[0]))
        self.commit("green: easier scenario")
        violations, _ = tt.gaming(self.repo, self.info())
        self.assertTrue(any("changes feature files" in v for v in violations), violations)

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


class AllureTest(RepoCase):
    def test_export(self):
        self.write("features/totals.feature", "\n".join([
            "Feature: Invoice totals",
            "  @AC-1", "  Scenario: Totals are rounded to cents",
            "    Given an invoice with lines 0.1 and 0.2", "    When the total is computed", "    Then the total is 0.30",
            "  @AC-2", "  Scenario: An empty invoice is rejected",
            "    Given an invoice with no lines", "    When the total is computed", '    Then it fails with "empty invoice"',
        ]) + "\n")
        self.write("plan/brief.md", BRIEF.replace("features/currency.feature", "features/totals.feature"))
        self.write("tests/test_invoice.py", "# AC-2: edge case\ndef test_old():\n    assert True\n")
        self.commit("plan: totals")
        cases = tt.parse_junit(FIXTURES / "vitest-cucumber-junit.xml")
        cases.append({"file": "tests/test_invoice.py", "name": "test_old", "status": "passed", "kind": None,
                      "message": "", "location": "", "time": 0.01, "trace": ""})
        log = self.repo / "run.log"
        log.write_text("x")
        out = tt.allure_export(self.repo, {**self.info(), "bdd": "vitest-cucumber"}, "red", cases, set(), log)
        results = {r["name"]: r for r in (json.loads(f.read_text()) for f in out.glob("*-result.json"))}
        self.assertEqual(set(results), {"Totals are rounded to cents", "An empty invoice is rejected", "test_old"})
        rounded = results["Totals are rounded to cents"]
        self.assertEqual(rounded["status"], "failed")
        self.assertEqual([s["status"] for s in rounded["steps"]], ["passed", "passed", "failed"])
        tags = {l["value"] for l in rounded["labels"] if l["name"] == "tag"}
        self.assertEqual(tags, {"AC-1", "this-ticket"})
        self.assertIn({"name": "tag", "value": "AC-2"}, results["test_old"]["labels"])
        self.assertEqual(json.loads((out / "run.json").read_text())["label"], "red")
        statuses = tt.last_results(self.repo)
        self.assertEqual(statuses["features/totals.feature::Totals are rounded to cents"], "failed")
        # The same test keeps its history id from run to run.
        again = tt.allure_export(self.repo, {**self.info(), "bdd": "vitest-cucumber"}, "check", cases, set(), log)
        ids = lambda d: sorted(json.loads(f.read_text())["historyId"] for f in d.glob("*-result.json"))
        self.assertEqual(ids(out), ids(again))


class TeamReportTest(RepoCase):
    def test_history_then_report(self):
        calls = self.repo / "calls.txt"
        fake = self.repo / "fake-allure"
        fake.write_text("#!/bin/sh\n"
                        f"echo \"$@\" >> {calls}\n"
                        "if [ \"$1\" = generate ]; then\n"
                        f"  out=$(sed -n 's/.*\"output\": \"\\([^\"]*\\)\".*/\\1/p' \"$4\"); mkdir -p \"$out/awesome\"; touch \"$out/awesome/index.html\"\n"
                        "fi\n")
        fake.chmod(0o755)
        for name, stamp in (("20260101-000000-red", "2026-01-01T00:00:00"), ("20260101-000100-check", "2026-01-01T00:01:00")):
            self.write(f".team/allure/{name}/run.json", json.dumps({"label": name.split("-")[-1], "time": stamp, "epoch": int(name[9:15]),
                                                                     "commit": "abcdef123", "worktree": str(self.repo)}))
        reports = self.repo / "reports"
        old = tt.ALLURE, tt.REPORTS
        tt.ALLURE, tt.REPORTS = fake, reports
        self.addCleanup(lambda: setattr(tt, "ALLURE", old[0]) or setattr(tt, "REPORTS", old[1]))
        env = os.environ.pop("HERDR_SANDBOX", None)
        self.addCleanup(lambda: env and os.environ.__setitem__("HERDR_SANDBOX", env))
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(tt.team_report([str(self.repo)]), 0)
        lines = calls.read_text().splitlines()
        self.assertEqual([l.split()[0] for l in lines], ["history", "generate"])
        self.assertIn("20260101-000000-red", lines[0])
        self.assertIn("20260101-000100-check", lines[1])
        home = reports / self.repo.name
        self.assertIn('"appendHistory": true', (home / "allurerc.mjs").read_text())
        # Nothing new: regenerate without appending the newest run twice.
        with contextlib.redirect_stdout(io.StringIO()):
            tt.team_report([str(self.repo)])
        self.assertEqual([l.split()[0] for l in calls.read_text().splitlines()], ["history", "generate", "generate"])
        self.assertIn('"appendHistory": false', (home / "allurerc.mjs").read_text())

    def test_refuses_in_sandbox(self):
        os.environ["HERDR_SANDBOX"] = "1"
        self.addCleanup(os.environ.pop, "HERDR_SANDBOX")
        with self.assertRaises(tt.ToolError):
            tt.team_report([])


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
