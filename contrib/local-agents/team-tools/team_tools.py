#!/usr/bin/env python3
"""Deterministic tools for team roles: everything with a checkable answer.

One file, called by the name it is linked as (or `team_tools.py <tool>`):

  repo-facts     the repo's stack, commands and test-file patterns, as JSON
  setup-env      install dependencies when missing (npm ci, ...), then record
                 which tests already fail at the branch start
  run-tests      run tests, print a short summary; the full log goes to a file
  red-check      new tests and scenarios must fail on an assertion, not on a
                 load/type error or a step with no code
  ac-trace       every acceptance criterion (an @AC-n scenario) is bound to step
                 code or cited by a test; every new test cites one
  brief-lint     plan/brief.md has the sections and limits the planner owes, and
                 its feature files are well-formed Gherkin
  scope-check    changed files stay inside the brief's OWNED PATHS
  check-gaming   no test edits after RED, no feature edits outside "plan:"
                 commits, no new skips, no deleted tests
  check          the gate: tests, typecheck, lint, check-gaming (+ mutation)
  review-pack    one file for the reviewer: brief, scenarios, diff, check report, ac-trace
  team-report    (host only) the Allure HTML report of a worktree's runs, with history

Acceptance criteria are Gherkin scenarios: the brief's FEATURES section names
the .feature files, and each scenario the ticket adds or changes carries one
@AC-n tag. With pytest-bdd or @amiceli/vitest-cucumber in the repo the test
author binds scenarios to step code; without one, tests cite "AC-n" in a comment.
Tests that already fail when setup-env runs (say, ones needing a database the
sandbox can't reach) are saved as known failures: check reports them but blocks
only on new ones. Reports and logs go to .team/ in the worktree (ignored by git through
info/exclude); setup-env, red-check and check also leave Allure results in
.team/allure/. Commit subjects carry the TDD state: "plan: ...", "red: ...",
"green: ...", "refactor: ...". Exit status 0 means pass; 1 means the check failed; 2 means
the tool could not run; 3 (brief-lint) means the brief has open questions.
"""

import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

TOOLS = ("repo-facts", "setup-env", "run-tests", "red-check", "ac-trace", "brief-lint",
         "scope-check", "check-gaming", "check", "review-pack", "team-report")

BRIEF = Path("plan/brief.md")
SECTIONS = ("INTENT", "FEATURES", "GUARDRAILS", "OWNED PATHS", "TEST COMMAND", "CONTEXT POINTERS")
MAX_ACS = 8
REPORTS = Path(os.environ.get("TEAM_REPORTS", "~/.local/share/herdr-launch/reports")).expanduser()
ALLURE = Path(os.environ.get("TEAM_ALLURE", "~/.local/share/herdr-launch/allure-cli/node_modules/.bin/allure")).expanduser()
SUMMARY_FAILURES = 5
TAIL_LINES = 40

TEST_PATTERNS = {
    "node": ["**/*.test.*", "**/*.spec.*", "**/__tests__/**", "tests/**", "test/**"],
    "python": ["**/test_*.py", "**/*_test.py", "tests/**", "**/conftest.py"],
    "dotnet": ["**/*.Tests/**", "**/*.Test/**", "**/*Tests.cs"],
    "generic": ["tests/**", "test/**"],
}
SKIP_MARKERS = re.compile(
    r"\b(it|test|describe)\.(skip|todo)\b|\b(xit|xdescribe|xtest)\s*\(|@pytest\.mark\.(skip|xfail)"
    r"|\[(Skip|Ignore)\b|Skip\s*=\s*\"|\bpytest\.skip\("
)
TEST_DECL = re.compile(
    r"""^\s*(?:it|test)(?:\.each\([^)]*\))?\s*\(\s*(['"`])(?P<js>.+?)\1"""
    r"""|^\s*(?:async\s+)?def\s+(?P<py>test_\w+)"""
    r"""|^\s*public\s+(?:async\s+)?\w+\s+(?P<cs>\w+)\s*\("""
)
AC_REF = re.compile(r"\bAC-(\d+)\b")
SNAPSHOT = re.compile(r"(__snapshots__/|\.snap$|\.verified\.)")
ASSERTION_TYPES = ("AssertionError", "AssertError", "AssertionFailedError", "XunitException")


class ToolError(Exception):
    pass


# --- git and files -------------------------------------------------------------

def git(*args, check=True):
    result = subprocess.run(["git", *args], capture_output=True, text=True)
    if check and result.returncode != 0:
        raise ToolError(f"git {' '.join(args)}: {result.stderr.strip()}")
    return result.stdout


def root():
    return Path(git("rev-parse", "--show-toplevel").strip())


def team_dir(repo):
    """.team/ in the worktree, excluded from git once."""
    directory = repo / ".team"
    (directory / "logs").mkdir(parents=True, exist_ok=True)
    exclude = Path(git("rev-parse", "--path-format=absolute", "--git-path", "info/exclude").strip())
    lines = exclude.read_text().splitlines() if exclude.exists() else []
    if ".team/" not in lines:
        exclude.parent.mkdir(parents=True, exist_ok=True)
        with exclude.open("a") as handle:
            handle.write(".team/\n")
    return directory


def base(repo):
    """The commit the branch started from: TEAM_BASE, .team/base, else the
    merge-base with the default branch (saved, so later commits on main don't
    move it)."""
    if os.environ.get("TEAM_BASE"):
        return git("rev-parse", os.environ["TEAM_BASE"]).strip()
    saved = repo / ".team" / "base"
    if saved.exists():
        return saved.read_text().strip()
    for candidate in ("origin/HEAD", "main", "master"):
        found = git("merge-base", "HEAD", candidate, check=False).strip()
        if found:
            team_dir(repo)
            saved.write_text(found + "\n")
            return found
    raise ToolError("no base: set TEAM_BASE to the commit the branch started from")


def changed_files(repo, since):
    """Files changed since `since`: committed, staged, unstaged and untracked."""
    names = set(git("diff", "--name-only", since).split())
    names |= set(git("ls-files", "--others", "--exclude-standard").split())
    return sorted(n for n in names if not n.startswith(".team/") and "__pycache__/" not in n)


def glob_regex(pattern):
    """A path glob as a regex: ** spans directories, * and ? stay inside one."""
    out, i = "", 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out, i = out + "(?:.*/)?", i + 3
        elif pattern.startswith("**", i):
            out, i = out + ".*", i + 2
        elif pattern[i] == "*":
            out, i = out + "[^/]*", i + 1
        elif pattern[i] == "?":
            out, i = out + "[^/]", i + 1
        else:
            out, i = out + re.escape(pattern[i]), i + 1
    return re.compile(out + r"\Z")


def matches(path, patterns):
    path = path.removeprefix("./")
    for pattern in patterns:
        pattern = pattern.strip().removeprefix("./")
        if not pattern:
            continue
        if pattern.endswith("/"):
            pattern += "**"
        if glob_regex(pattern).match(path) or (not any(c in pattern for c in "*?") and path.startswith(pattern.rstrip("/") + "/")):
            return True
    return False


# --- repo facts ------------------------------------------------------------------

def facts(repo):
    """What the repo is and how to build and test it, from its own files."""
    info = {"root": str(repo), "stack": "generic", "package_manager": None, "runner": None, "bdd": None,
            "commands": {}, "test_patterns": TEST_PATTERNS["generic"], "setup": None,
            "agents_md": (repo / "AGENTS.md").exists() or (repo / "CLAUDE.md").exists()}
    package = repo / "package.json"
    if package.exists():
        data = json.loads(package.read_text())
        scripts = data.get("scripts", {})
        deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
        manager = next((m for lock, m in (("pnpm-lock.yaml", "pnpm"), ("yarn.lock", "yarn"),
                                          ("bun.lockb", "bun"), ("bun.lock", "bun"),
                                          ("package-lock.json", "npm")) if (repo / lock).exists()), "npm")
        run = f"{manager} run" if manager != "npm" else "npm run"
        commands = {}
        for name, key in (("test", "test"), ("typecheck", "typecheck"), ("lint", "lint"),
                          ("build", "build"), ("format_check", "format:check"), ("mutation", "test:mutation")):
            if key in scripts:
                commands[name] = f"{run} {key}"
        runner = "vitest" if "vitest" in deps or "vitest" in scripts.get("test", "") else (
            "jest" if "jest" in deps or "jest" in scripts.get("test", "") else None)
        install = {"npm": "npm ci", "pnpm": "pnpm install --frozen-lockfile",
                   "yarn": "yarn install --frozen-lockfile", "bun": "bun install --frozen-lockfile"}[manager]
        info.update(stack="node", package_manager=manager, runner=runner, commands=commands,
                    test_patterns=TEST_PATTERNS["node"],
                    bdd="vitest-cucumber" if runner == "vitest" and "@amiceli/vitest-cucumber" in deps else None,
                    setup=None if (repo / "node_modules").exists() else install)
    elif any((repo / f).exists() for f in ("pyproject.toml", "pytest.ini", "setup.cfg", "tox.ini")):
        commands = {"test": "python -m pytest -q"}
        pyproject = (repo / "pyproject.toml").read_text() if (repo / "pyproject.toml").exists() else ""
        if "[tool.ruff" in pyproject:
            commands["lint"] = "ruff check ."
        if "[tool.mypy" in pyproject:
            commands["typecheck"] = "mypy ."
        if "mutmut" in pyproject:
            commands["mutation"] = "mutmut run"
        declared = pyproject + "".join(p.read_text(errors="replace") for p in repo.glob("requirements*.txt"))
        bdd = "pytest-bdd" if re.search(r"pytest[-_]bdd", declared) else None
        info.update(stack="python", runner="pytest", commands=commands, test_patterns=TEST_PATTERNS["python"], bdd=bdd)
    elif list(repo.glob("*.sln")) or list(repo.glob("*.csproj")):
        info.update(stack="dotnet", runner="dotnet", test_patterns=TEST_PATTERNS["dotnet"],
                    commands={"test": "dotnet test", "build": "dotnet build"})
    makefile = repo / "Makefile"
    if makefile.exists():
        targets = set(re.findall(r"^([A-Za-z][\w-]*):", makefile.read_text(), re.M))
        for name in ("test", "lint", "typecheck", "build"):
            if name in targets and name not in info["commands"]:
                info["commands"][name] = f"make {name}"
    return info


# --- running tests ---------------------------------------------------------------

def test_command(info, files, junit):
    """Command that runs `files` (all when empty) and writes JUnit XML to
    `junit`, or None when the runner can't produce JUnit (then plain run)."""
    if info["runner"] == "vitest":
        prefix = {"npm": ["npx"], "pnpm": ["pnpm", "exec"], "yarn": ["yarn"], "bun": ["bunx"]}[info["package_manager"]]
        return prefix + ["vitest", "run", *files, "--reporter=dot", "--reporter=junit",
                         f"--outputFile.junit={junit}"]
    if info["runner"] == "pytest":
        return [sys.executable, "-m", "pytest", "-q", f"--junitxml={junit}", *files]
    return None


def parse_junit(path):
    """Each test case as {file, name, status, kind, message, location}.

    status: passed | failed | skipped. kind (failed only): assertion when the
    test ran and an assertion failed, error when it threw something else,
    load when the file never loaded (the case is the file itself)."""
    cases = []
    tree = ET.parse(path)
    for case in tree.iter("testcase"):
        file = case.get("classname") or case.get("file") or ""
        name = case.get("name") or ""
        problem = case.find("failure")
        if problem is None:
            problem = case.find("error")
        entry = {"file": file, "name": name, "status": "passed", "kind": None, "message": "", "location": "",
                 "time": float(case.get("time") or 0), "trace": ""}
        if case.find("skipped") is not None:
            entry["status"] = "skipped"
        elif problem is not None:
            kind_name = problem.get("type") or ""
            message = problem.get("message") or ""
            text = problem.text or ""
            located = re.search(r"❯ (\S+:\d+:\d+)|^\s*(\S+\.py:\d+)", text, re.M)
            if name == file:
                kind = "load"
            elif (any(t in kind_name for t in ASSERTION_TYPES) or message.startswith(("expected ", "assert ", "AssertionError"))
                  or (problem.tag == "failure" and not kind_name and message.startswith("assert"))):
                kind = "assertion"
            else:
                kind = "error"
            entry.update(status="failed", kind=kind, message=(f"{kind_name}: {message}".strip(": ").splitlines() or [""])[0][:200],
                         location=(located.group(1) or located.group(2)) if located else "",
                         trace=(text.strip() or message)[:4000])
        cases.append(entry)
    return cases


def run_logged(command, log, shell=False):
    with log.open("w") as handle:
        handle.write(f"$ {command if shell else shlex.join(command)}\n")
        handle.flush()
        result = subprocess.run(command, stdout=handle, stderr=subprocess.STDOUT, shell=shell, text=True)
    return result.returncode


def tail(log, lines=TAIL_LINES):
    text = log.read_text(errors="replace").splitlines()
    return "\n".join(text[-lines:])


def run_tests(repo, info, files, label="run"):
    """Run tests; returns (exit code, cases or None, log path)."""
    directory = team_dir(repo)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    log = directory / "logs" / f"{label}-{stamp}.log"
    junit = directory / "logs" / f"{label}-{stamp}.xml"
    command = test_command(info, files, junit)
    if command is None:
        if not info["commands"].get("test"):
            raise ToolError("no test command found; name one in AGENTS.md or plan/brief.md")
        line = info["commands"]["test"] + ("" if not files else " " + shlex.join(files))
        return run_logged(line, log, shell=True), None, log
    code = run_logged(command, log)
    cases = parse_junit(junit) if junit.exists() else None
    return code, cases, log


def case_id(case):
    return f"{case['file']} > {case['name']}"


def known_failures(repo):
    path = repo / ".team" / "known-failures.json"
    return set(json.loads(path.read_text())) if path.exists() else set()


def record_baseline(repo, info):
    """Run the suite once and save the tests that fail before any work."""
    code, cases, log = run_tests(repo, info, [], label="baseline")
    failing = sorted(case_id(c) for c in cases or [] if c["status"] == "failed")
    (team_dir(repo) / "known-failures.json").write_text(json.dumps(failing, indent=2) + "\n")
    return code, cases, log, failing


def tests_ok(code, cases, known):
    """Green when the run passed, or when it failed only on known failures."""
    if code == 0 or cases is None:
        return code == 0
    failed = [c for c in cases if c["status"] == "failed"]
    return bool(failed) and all(case_id(c) in known for c in failed)


def summarize(code, cases, log, known=frozenset()):
    lines = []
    if cases is None:
        lines.append(f"tests: exit {code} (no per-test report)")
        if code:
            lines.append(tail(log, 15))
    else:
        counts = {s: sum(1 for c in cases if c["status"] == s) for s in ("passed", "failed", "skipped")}
        lines.append(f"tests: {counts['passed']} passed, {counts['failed']} failed, {counts['skipped']} skipped")
        failed = [c for c in cases if c["status"] == "failed" and case_id(c) not in known]
        already = counts["failed"] - len(failed)
        if already:
            lines.append(f"  {already} of the failures were already failing at the branch start (known failures, not blocking)")
        for case in failed[:SUMMARY_FAILURES]:
            lines.append(f"  FAIL {case['file']} > {case['name']}: {case['message']}"
                         + (f" ({case['location']})" if case["location"] else ""))
        if len(failed) > SUMMARY_FAILURES:
            lines.append(f"  ... {len(failed) - SUMMARY_FAILURES} more")
        if code and not counts["failed"]:
            lines.append(f"exit {code} with no failing test; last lines:\n{tail(log, 15)}")
    lines.append(f"log: {log}")
    return "\n".join(lines)


# --- brief -----------------------------------------------------------------------

def brief_sections(text):
    """Section name -> body. Headings are '## NAME' or 'NAME:' lines."""
    sections, current = {}, None
    for line in text.splitlines():
        heading = re.match(r"^#{1,4}\s*([A-Za-z][A-Za-z ]+?)\s*:?\s*$|^([A-Z][A-Z ]+):\s*(.*)$", line)
        if heading:
            current = (heading.group(1) or heading.group(2)).strip().upper()
            sections[current] = []
            if heading.group(3):
                sections[current].append(heading.group(3))
        elif current:
            sections[current].append(line)
    return {k: "\n".join(v).strip() for k, v in sections.items()}


def section_paths(text, section):
    """The first word of each line of a section: one path or glob per line."""
    body = brief_sections(text).get(section, "")
    paths = []
    for line in body.splitlines():
        line = line.strip().lstrip("-*").strip()
        if line:
            paths.append(line.split()[0].strip("`,"))
    return paths


def owned_paths(text):
    return section_paths(text, "OWNED PATHS")


def brief_features(text):
    return section_paths(text, "FEATURES")


# --- gherkin -----------------------------------------------------------------------

SCENARIO_LINE = re.compile(r"^\s*(Scenario Outline|Scenario Template|Scenario|Example):\s*(.*?)\s*$")
STEP_LINE = re.compile(r"^\s*(Given|When|Then|And|But|\*)\s+(.*?)\s*$")
AC_TAG = re.compile(r"^@AC-(\d+)$")


def parse_feature(text):
    """{name, scenarios} from Gherkin. A scenario is {name, line, tags, steps,
    outline, examples}: steps as (keyword, text), examples the number of
    Examples rows. Background steps belong to no scenario."""
    feature, scenarios, tags, current = None, [], [], None
    docstring = in_examples = header_seen = False
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if docstring:
            docstring = not line.startswith(('"""', "```"))
            continue
        if line.startswith(('"""', "```")):
            docstring = True
            continue
        if not line or line.startswith("#"):
            continue
        if line.startswith("@"):
            tags += [t for t in line.split("#")[0].split() if t.startswith("@")]
            continue
        if line.startswith("Feature:"):
            feature, tags = line[8:].strip(), []
            continue
        if line.startswith(("Rule:", "Background:")):
            current, tags, in_examples = None, [], False
            continue
        scenario = SCENARIO_LINE.match(raw)
        if scenario:
            current = {"name": scenario.group(2), "line": number, "tags": tags, "steps": [],
                       "outline": scenario.group(1) != "Scenario" and scenario.group(1) != "Example", "examples": 0}
            scenarios.append(current)
            tags, in_examples = [], False
            continue
        if line.startswith(("Examples:", "Scenarios:")):
            in_examples, header_seen, tags = True, False, []
            continue
        if line.startswith("|"):
            if current and in_examples:
                current["examples"] += header_seen
                header_seen = True
            continue
        step = STEP_LINE.match(raw)
        if step and current and not in_examples:
            current["steps"].append((step.group(1), step.group(2)))
    return {"name": feature, "scenarios": scenarios}


def step_kinds(steps):
    """Given/When/Then for each step, And and But taking the one before."""
    kinds, last = [], "Given"
    for keyword, _ in steps:
        last = keyword if keyword in ("Given", "When", "Then") else last
        kinds.append(last)
    return kinds


def feature_files(repo):
    out = git("ls-files", "-co", "--exclude-standard", "--", "*.feature")
    return [f for f in out.split() if (repo / f).is_file()]


def scenario_index(repo):
    """Every scenario in the repo's feature files, with its file and feature."""
    index = []
    for path in feature_files(repo):
        parsed = parse_feature((repo / path).read_text(errors="replace"))
        index += [{**s, "file": path, "feature": parsed["name"]} for s in parsed["scenarios"]]
    return index


def ticket_scenarios(repo, text):
    """(scenarios, problems): the scenarios this ticket adds or changes in the
    brief's FEATURES files (new, or steps differ from the branch start), each
    with its AC numbers."""
    since = base(repo)
    found, problems = [], []
    for path in brief_features(text):
        file = repo / path
        if not path.endswith(".feature"):
            problems.append(f"FEATURES lists {path}, which is not a .feature file")
            continue
        if not file.is_file():
            problems.append(f"feature file {path} not found")
            continue
        parsed = parse_feature(file.read_text(errors="replace"))
        if not parsed["name"]:
            problems.append(f"{path}: no 'Feature:' line")
        old_text = git("show", f"{since}:{path}", check=False)
        old = {(s["name"], tuple(s["steps"])) for s in parse_feature(old_text)["scenarios"]}
        for scenario in parsed["scenarios"]:
            if (scenario["name"], tuple(scenario["steps"])) in old:
                continue
            acs = [int(m.group(1)) for t in scenario["tags"] if (m := AC_TAG.match(t))]
            found.append({**scenario, "file": path, "feature": parsed["name"], "acs": acs})
    return found, problems


def lint_features(repo, text):
    """Problems with the brief's feature files: what each new or changed
    scenario owes, and the AC numbering across them."""
    scenarios, problems = ticket_scenarios(repo, text)
    if not brief_features(text):
        return problems
    if not scenarios and not problems:
        problems.append("the FEATURES files add or change no scenario")
    names = {}
    for path in brief_features(text):
        if (repo / path).is_file():
            for scenario in parse_feature((repo / path).read_text(errors="replace"))["scenarios"]:
                names.setdefault((path, scenario["name"]), []).append(scenario["line"])
    for (path, name), lines in names.items():
        if len(lines) > 1:
            problems.append(f"{path}: scenario name \"{name}\" is used {len(lines)} times (lines {', '.join(map(str, lines))}); names must be unique")
    for scenario in scenarios:
        where = f"{scenario['file']}:{scenario['line']} \"{scenario['name']}\""
        if not scenario["name"]:
            problems.append(f"{where}: scenario has no name")
        if len(scenario["acs"]) != 1:
            problems.append(f"{where}: needs exactly one @AC-n tag, has {len(scenario['acs'])}")
        kinds = step_kinds(scenario["steps"])
        if "When" not in kinds:
            problems.append(f"{where}: no When step (the action under test)")
        if "Then" not in kinds:
            problems.append(f"{where}: no Then step (the observable outcome)")
        if scenario["outline"] and not scenario["examples"]:
            problems.append(f"{where}: Scenario Outline without Examples rows")
    acs = sorted(ac for s in scenarios for ac in s["acs"])
    for ac in sorted(set(acs)):
        if acs.count(ac) > 1:
            problems.append(f"AC-{ac} tags {acs.count(ac)} scenarios; one scenario per criterion "
                            "(variations go in Examples rows, edge cases in the test author's tests)")
    distinct = sorted(set(acs))
    if len(distinct) > MAX_ACS:
        problems.append(f"{len(distinct)} acceptance criteria; at most {MAX_ACS} (split the task)")
    if distinct and distinct != list(range(1, len(distinct) + 1)):
        problems.append(f"acceptance criteria must be numbered AC-1..AC-{len(distinct)}, found {['AC-%d' % a for a in distinct]}")
    return problems


def python_name(text):
    """pytest-bdd's test function name for a scenario, without the test_ prefix."""
    return re.sub(r"^\d+_*", "", re.sub(r"\W", "", text.replace(" ", "_"))).lower()


VITEST_SCENARIO = re.compile(r"(?:^|> )(?:Scenario(?: Outline| Template)?|Example): (?P<name>.+?)(?: > (?P<step>.+))?$")
NOT_CALLED = re.compile(r"(?:Scenario(?: Outline| Template)?|Example): (?P<name>.+?) was not called")


def scenario_of(case, index, info):
    """(scenario, step) for a JUnit case that runs a Gherkin scenario, else
    (None, None). vitest-cucumber reports one case per step, pytest-bdd one per
    scenario; a vitest-cucumber scenario with no code shows up as a file error."""
    name = case["name"]
    feature = re.match(r"Feature: (.+?) > ", name)
    found = VITEST_SCENARIO.search(name) or NOT_CALLED.search(case.get("message") or "")
    if found:
        options = [s for s in index if s["name"] == found.group("name")
                   and (not feature or s["feature"] == feature.group(1))]
        step = found.groupdict().get("step")
        return (options[0], step) if options else (None, None)
    if info.get("bdd") == "pytest-bdd" and name.startswith("test_"):
        slug = re.sub(r"\[.*\]$", "", name)[5:]
        for candidate in (slug, re.sub(r"_\d+$", "", slug)):
            options = [s for s in index if python_name(s["name"]) == candidate]
            if options:
                return options[0], None
    return None, None


def case_status(case):
    """Allure status: failed is an assertion that failed, broken anything else."""
    if case["status"] != "failed":
        return case["status"]
    return "failed" if case["kind"] == "assertion" else "broken"


STATUS_RANK = {"passed": 0, "skipped": 1, "unknown": 2, "failed": 3, "broken": 4}


def group_cases(cases, index, info):
    """(scenarios, plain): JUnit cases that run scenarios, grouped per
    scenario as {(file, name): {scenario, cases: [(case, step)], status}}, and
    the other cases."""
    groups, plain = {}, []
    for case in cases:
        scenario, step = scenario_of(case, index, info)
        if scenario is None:
            plain.append(case)
            continue
        group = groups.setdefault((scenario["file"], scenario["name"]), {"scenario": scenario, "cases": [], "status": "passed"})
        group["cases"].append((case, step))
        if STATUS_RANK[case_status(case)] > STATUS_RANK[group["status"]]:
            group["status"] = case_status(case)
    return groups, plain


def scenario_bindings(repo, info, scenario):
    """Test files that bind a scenario to step code: they name its feature
    file and either the scenario or (pytest-bdd) load the whole file."""
    hits = git("grep", "--untracked", "-l", "-F", Path(scenario["file"]).name, check=False).split()
    bound = []
    for path in hits:
        if path.endswith(".feature") or path.startswith(".team/") or not matches(path, info["test_patterns"]):
            continue
        text = (repo / path).read_text(errors="replace")
        if scenario["name"] in text or (info.get("bdd") == "pytest-bdd" and re.search(r"\bscenarios\s*\(", text)):
            bound.append(path)
    return bound


def lint_brief(repo, text):
    """(problems, questions): problems block the hand-off; questions mean the
    planner must stop and ask."""
    problems = []
    sections = brief_sections(text)
    for name in SECTIONS:
        if not sections.get(name):
            problems.append(f"missing or empty section: {name}")
    if sections.get("DONE WHEN"):
        problems.append("DONE WHEN is replaced by FEATURES: acceptance criteria are @AC-n scenarios in .feature files")
    problems += lint_features(repo, text)
    for path in owned_paths(text):
        literal = re.split(r"[*?]", path)[0].rstrip("/") or "."
        probe = repo / literal
        if not probe.exists() and not probe.parent.exists():
            problems.append(f"owned path {path}: neither it nor its parent folder exists")
    command = sections.get("TEST COMMAND", "").strip().strip("`").splitlines()
    if command:
        first = shlex.split(command[0].strip("`"))[:1]
        if first and not shutil.which(first[0]) and not (repo / first[0]).exists():
            problems.append(f"test command: {first[0]} not found on PATH")
    questions = sections.get("OPEN QUESTIONS", "")
    open_questions = [q for q in questions.splitlines() if q.strip() and q.strip().lstrip("-* ").lower() not in ("none", "n/a", "")]
    return problems, open_questions


# --- tests and acceptance criteria ----------------------------------------------

def test_declarations(path):
    """(line number, test name, cited ACs) per test declared in a file; a test
    cites an AC on its own line, in its name, or in the comment lines above."""
    lines = path.read_text(errors="replace").splitlines()
    found = []
    for index, line in enumerate(lines):
        match = TEST_DECL.match(line)
        if not match:
            continue
        name = match.group("js") or match.group("py") or match.group("cs")
        if match.group("cs") and not any("[Fact" in l or "[Theory" in l or "[Test" in l for l in lines[max(0, index - 3):index]):
            continue
        context = [line]
        back = index - 1
        while back >= 0 and back >= index - 6 and re.match(r"^\s*(//|#|\*|/\*|\[)", lines[back]):
            context.append(lines[back])
            back -= 1
        acs = sorted({int(m) for text in context for m in AC_REF.findall(text)})
        found.append((index + 1, name, acs))
    return found


def last_results(repo):
    """Scenario statuses from the last red-check or check: {"file::name": status}."""
    path = repo / ".team" / "scenario-results.json"
    return json.loads(path.read_text()) if path.exists() else {}


def trace(repo, info):
    brief = repo / BRIEF
    if not brief.exists():
        raise ToolError(f"{BRIEF} not found")
    scenarios, _ = ticket_scenarios(repo, brief.read_text())
    acs = sorted({ac for s in scenarios for ac in s["acs"]})
    since = base(repo)
    tests = [f for f in changed_files(repo, since) if matches(f, info["test_patterns"])
             and not f.endswith(".feature") and (repo / f).is_file()]
    covered = {ac: [] for ac in acs}
    uncited, unknown, unbound = [], [], []
    results = last_results(repo)
    for scenario in scenarios:
        bound = scenario_bindings(repo, info, scenario)
        status = results.get(f"{scenario['file']}::{scenario['name']}")
        label = (f"scenario \"{scenario['name']}\" ({scenario['file']}:{scenario['line']})"
                 + (f" bound in {', '.join(bound)}" if bound else "") + (f" [last run: {status}]" if status else ""))
        if not bound:
            unbound.append(label)
            continue
        for ac in scenario["acs"]:
            covered[ac].append(label)
    for file in tests:
        for line, name, cited in test_declarations(repo / file):
            label = f"{file}:{line} {name}"
            if not cited:
                uncited.append(label)
            for ac in cited:
                if ac in covered:
                    covered[ac].append(label)
                else:
                    unknown.append(f"{label} cites AC-{ac}, which the brief does not define")
    return {"acs": acs, "covered": {f"AC-{k}": v for k, v in covered.items()},
            "uncovered": [f"AC-{k}" for k, v in covered.items() if not v],
            "uncited": uncited, "unknown": unknown, "test_files": tests,
            "unbound": unbound if info.get("bdd") else [], "bdd": info.get("bdd")}


# --- gaming ----------------------------------------------------------------------

def commits_since(since):
    """(sha, subject) oldest first."""
    out = git("log", "--reverse", "--format=%H%x09%s", f"{since}..HEAD")
    return [tuple(line.split("\t", 1)) for line in out.splitlines() if "\t" in line]


def gaming(repo, info):
    """Violations (block) and hints (for the reviewer)."""
    since = base(repo)
    violations, hints = [], []
    commits = commits_since(since)
    red = [i for i, (_, subject) in enumerate(commits) if subject.lower().startswith("red:")]
    is_test = lambda f: matches(f, info["test_patterns"]) and not f.endswith(".feature")
    if red:
        after = commits[red[-1] + 1:]
        for sha, subject in after:
            touched = git("diff-tree", "--no-commit-id", "--name-only", "-r", sha).split()
            tests = [t for t in touched if is_test(t)]
            if tests:
                violations.append(f"{sha[:8]} \"{subject}\" changes tests after RED: {', '.join(tests)}")
        dirty = [f for f in git("diff", "--name-only", "HEAD").split() if is_test(f)]
        if dirty and not commits[-1][1].lower().startswith("red:"):
            violations.append(f"uncommitted test changes after RED: {', '.join(dirty)}")
    # Scenarios are the planner's: only "plan:" commits may change them.
    for sha, subject in commits:
        if subject.lower().startswith("plan:"):
            continue
        touched = git("diff-tree", "--no-commit-id", "--name-only", "-r", sha).split()
        features = [t for t in touched if t.endswith(".feature")]
        if features:
            violations.append(f"{sha[:8]} \"{subject}\" changes feature files, which only the planner's \"plan:\" commits may: {', '.join(features)}")
    dirty_features = [f for f in set(git("diff", "--name-only", "HEAD").split()) | set(git("ls-files", "--others", "--exclude-standard").split())
                      if f.endswith(".feature")]
    if dirty_features:
        violations.append(f"uncommitted feature file changes (scenarios are the planner's): {', '.join(sorted(dirty_features))}")
    diff = git("diff", "--unified=0", "--src-prefix=a/", "--dst-prefix=b/", since)
    current = None
    removed_tests, added_tests = [], []
    for line in diff.splitlines():
        if line.startswith("+++ "):
            current = line[6:] if line.startswith("+++ b/") else None
            continue
        if line.startswith("--- "):
            continue
        if current is None:
            continue
        is_test = matches(current, info["test_patterns"])
        if line.startswith("+") and SKIP_MARKERS.search(line):
            violations.append(f"{current}: adds a skip marker: {line[1:].strip()[:120]}")
        if is_test:
            declared = TEST_DECL.match(line[1:])
            if declared:
                name = declared.group("js") or declared.group("py") or declared.group("cs")
                (added_tests if line.startswith("+") else removed_tests).append((current, name))
        if SNAPSHOT.search(current) and line[:1] in "+-":
            violations.append(f"{current}: snapshot or approved file changed")
            current = None
    added_names = {name for _, name in added_tests}
    for file, name in removed_tests:
        if name not in added_names:
            violations.append(f"{file}: removes test \"{name}\"")
    for name in git("diff", "--name-only", "--diff-filter=D", since).split():
        if matches(name, info["test_patterns"]):
            violations.append(f"{name}: test file deleted")
    # Heuristic: literals the new tests expect that also appear in new source lines.
    literal = re.compile(r"""(['"])([^'"\n]{5,60})\1|\b(\d{3,}(?:\.\d+)?)\b""")
    test_literals, source_lines = set(), []
    current = None
    for line in diff.splitlines():
        if line.startswith("+++ "):
            current = line[6:] if line.startswith("+++ b/") else None
        elif current and line.startswith("+") and not line.startswith("+++"):
            if matches(current, info["test_patterns"]):
                if ("expect" in line or "assert" in line.lower()) and not re.match(r"^\+\s*(import|from)\b", line):
                    test_literals |= {m.group(2) or m.group(3) for m in literal.finditer(line)}
            elif not current.startswith("plan/") and not current.endswith((".md", ".txt", ".feature")):
                source_lines.append((current, line[1:]))
    for file, line in source_lines:
        for value in test_literals:
            if value and value in line and not re.match(r"^\s*(//|#|\*|import|from)", line):
                hints.append(f"{file}: new source line contains a value a test expects ({value!r}): {line.strip()[:100]}")
                break
    return violations, hints[:20]


# --- allure ------------------------------------------------------------------------

def stable_id(text):
    return hashlib.md5(text.encode()).hexdigest()


def cited_acs(repo, cases):
    """{(file, test name): [AC numbers]} for plain test cases whose file exists."""
    cited = {}
    for file in {c["file"] for c in cases}:
        path = repo / file
        if not path.is_file():
            path = repo / (file.replace(".", "/") + ".py")
        if path.is_file():
            for _, name, acs in test_declarations(path):
                cited[(file, name)] = acs
    return cited


def allure_export(repo, info, label, cases, known, log):
    """Allure result files for one run, in .team/allure/<stamp>-<label>/;
    team-report turns them into the HTML report. A scenario is one result with
    its Given/When/Then lines as steps; other tests are one result each."""
    directory = team_dir(repo)
    now = time.time()
    out = directory / "allure" / f"{time.strftime('%Y%m%d-%H%M%S', time.localtime(now))}-{int(now * 1000) % 1000:03d}-{label}"
    out.mkdir(parents=True, exist_ok=True)
    index = scenario_index(repo)
    ticket = set()
    if (repo / BRIEF).exists():
        ticket = {(s["file"], s["name"]) for s in ticket_scenarios(repo, (repo / BRIEF).read_text())[0]}
    groups, plain = group_cases(cases, index, info)
    end = int(log.stat().st_mtime * 1000)
    clock = [end - int(sum(c["time"] for c in cases) * 1000)]

    def write(name, full_name, status, cases_in, labels, steps, description):
        start = clock[0]
        clock[0] += int(sum(c["time"] for c in cases_in) * 1000)
        failing = next((c for c in cases_in if c["status"] == "failed"), None)
        result = {"uuid": str(uuid.uuid4()), "historyId": stable_id(full_name), "testCaseId": stable_id(full_name),
                  "fullName": full_name, "name": name, "status": status, "stage": "finished",
                  "statusDetails": {"message": failing["message"] if failing else "", "trace": failing["trace"] if failing else "",
                                    "known": any(case_id(c) in known for c in cases_in if c["status"] == "failed")},
                  "start": start, "stop": clock[0], "labels": labels, "steps": steps, "description": description}
        (out / f"{result['uuid']}-result.json").write_text(json.dumps(result, indent=1))

    statuses = {}
    for (file, name), group in groups.items():
        scenario = group["scenario"]
        by_step = {step: case for case, step in group["cases"] if step}
        steps = []
        for keyword, text in scenario["steps"]:
            case = by_step.get(f"{keyword} {text}")
            status = case_status(case) if case else ("passed" if group["status"] == "passed" else "unknown")
            step = {"name": f"{keyword} {text}", "status": status, "stage": "finished"}
            if case and case["status"] == "failed":
                step["statusDetails"] = {"message": case["message"], "trace": case["trace"]}
            steps.append(step)
        tags = [t.lstrip("@") for t in scenario["tags"]] + (["this-ticket"] if (file, name) in ticket else [])
        labels = ([{"name": "parentSuite", "value": "Scenarios"}, {"name": "suite", "value": scenario["feature"] or file},
                   {"name": "feature", "value": scenario["feature"] or file}, {"name": "story", "value": name},
                   {"name": "package", "value": file}, {"name": "framework", "value": info.get("bdd") or "gherkin"}]
                  + [{"name": "tag", "value": t} for t in tags])
        description = f"`{file}:{scenario['line']}`\n\n" + "\n".join(f"**{k}** {t}  " for k, t in scenario["steps"])
        write(name, f"{file}: {name}", group["status"], [c for c, _ in group["cases"]], labels, steps, description)
        statuses[f"{file}::{name}"] = group["status"]
    for file, name in ticket - set(groups):
        statuses[f"{file}::{name}"] = "not run"
    cited = cited_acs(repo, plain)
    for case in plain:
        acs = cited.get((case["file"], case["name"]), [])
        labels = ([{"name": "parentSuite", "value": "Tests"}, {"name": "suite", "value": case["file"]},
                   {"name": "package", "value": case["file"]}, {"name": "framework", "value": info.get("runner") or "tests"}]
                  + [{"name": "tag", "value": f"AC-{ac}"} for ac in acs])
        write(case["name"], case_id(case), case_status(case), [case], labels, [], f"`{case['location']}`" if case["location"] else "")
    commit = git("rev-parse", "HEAD").strip()
    role = os.environ.get("HERDR_TEAM_ROLE", "")
    branch = os.environ.get("HERDR_TEAM_BRANCH") or git("branch", "--show-current", check=False).strip()
    meta = {"label": label, "time": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now)), "epoch": now, "commit": commit, "role": role, "branch": branch,
            "worktree": str(repo)}
    (out / "environment.properties").write_text("".join(f"{k}={v}\n" for k, v in meta.items() if v))
    (out / "executor.json").write_text(json.dumps({"name": "herdr team", "type": "herdr",
                                                   "buildName": f"{label} {commit[:8]}" + (f" ({role})" if role else "")}))
    (out / "run.json").write_text(json.dumps(meta, indent=2) + "\n")
    previous = last_results(repo)
    (directory / "scenario-results.json").write_text(json.dumps({**previous, **statuses}, indent=2) + "\n")
    return out


def export_quietly(repo, info, label, cases, known, log):
    """allure_export for the tools: the report is a by-product, so a failure
    becomes a line in the output, never a failed check."""
    if not cases:
        return "allure: no per-test report from this runner"
    try:
        return f"allure: {allure_export(repo, info, label, cases, known, log)}"
    except Exception as error:  # noqa: BLE001
        return f"allure: export failed ({type(error).__name__}: {error})"


# --- check -------------------------------------------------------------------------

def check(repo, info, mutation=False):
    directory = team_dir(repo)
    report = {"time": time.strftime("%Y-%m-%dT%H:%M:%S"), "commit": git("rev-parse", "HEAD").strip(),
              "dirty": bool(git("status", "--porcelain", "--untracked-files=no").strip()), "steps": {}}
    code, cases, log = run_tests(repo, info, [], label="check-tests")
    known = known_failures(repo)
    passed = sum(1 for c in cases if c["status"] == "passed") if cases else None
    report["steps"]["tests"] = {"ok": tests_ok(code, cases, known), "summary": summarize(code, cases, log, known), "passed": passed}
    report["allure"] = export_quietly(repo, info, "check", cases, known, log)
    for step in ("typecheck", "lint"):
        command = info["commands"].get(step)
        if not command:
            continue
        step_log = directory / "logs" / f"check-{step}-{time.strftime('%Y%m%d-%H%M%S')}.log"
        step_code = run_logged(command, step_log, shell=True)
        report["steps"][step] = {"ok": step_code == 0,
                                 "summary": f"{step}: {'ok' if step_code == 0 else 'FAILED'}\n"
                                            + ("" if step_code == 0 else tail(step_log, 20) + "\n") + f"log: {step_log}"}
    violations, hints = gaming(repo, info)
    report["steps"]["gaming"] = {"ok": not violations, "summary": "\n".join(
        ["gaming: none found" if not violations else "gaming: BLOCKING"] + [f"  {v}" for v in violations])}
    report["hints"] = hints
    if mutation and info["commands"].get("mutation"):
        mutation_log = directory / "logs" / f"check-mutation-{time.strftime('%Y%m%d-%H%M%S')}.log"
        mutation_code = run_logged(info["commands"]["mutation"], mutation_log, shell=True)
        score = re.findall(r"(?:Mutation score|All files)\D*?(\d+(?:\.\d+)?)", mutation_log.read_text(errors="replace"))
        report["steps"]["mutation"] = {"ok": mutation_code == 0, "score": float(score[-1]) if score else None,
                                       "summary": f"mutation: {'ok' if mutation_code == 0 else 'FAILED'}"
                                                  + (f", score {score[-1]}" if score else "") + f"\nlog: {mutation_log}"}
    report["green"] = all(step["ok"] for step in report["steps"].values())
    last_green_file = directory / "last-green.json"
    if report["green"] and not report["dirty"]:
        last = {"commit": report["commit"], "time": report["time"], "tests_passed": passed,
                "mutation_score": report["steps"].get("mutation", {}).get("score")}
        last_green_file.write_text(json.dumps(last, indent=2) + "\n")
    report["last_green"] = json.loads(last_green_file.read_text()) if last_green_file.exists() else None
    (directory / "check.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def format_check(report):
    lines = [f"CHECK {'GREEN' if report['green'] else 'RED'} at {report['commit'][:8]}"
             + (" (uncommitted changes present: commit before handing off)" if report["dirty"] else "")]
    lines += [step["summary"] for step in report["steps"].values()]
    if report.get("hints"):
        lines.append("hints for the reviewer (not blocking):")
        lines += [f"  {h}" for h in report["hints"]]
    last = report.get("last_green")
    lines.append("last green: " + (f"{last['commit'][:8]} ({last['time']}, {last['tests_passed']} tests passed"
                                   + (f", mutation {last['mutation_score']}" if last.get("mutation_score") is not None else "")
                                   + ")" if last else "none yet"))
    return "\n".join(lines)


# --- review pack -------------------------------------------------------------------

def review_pack(repo, info):
    directory = team_dir(repo)
    since = base(repo)
    parts = ["# Review pack", ""]
    brief = repo / BRIEF
    parts += ["## Brief", "", brief.read_text() if brief.exists() else "(no plan/brief.md)", ""]
    for path in brief_features(brief.read_text()) if brief.exists() else []:
        text = (repo / path).read_text(errors="replace") if (repo / path).is_file() else "(missing)"
        parts += [f"## Scenarios: {path}", "", "```gherkin", text.strip(), "```", ""]
    check_file = directory / "check.json"
    if check_file.exists():
        report = json.loads(check_file.read_text())
        head = git("rev-parse", "HEAD").strip()
        stale = "" if report["commit"] == head else f" (STALE: ran at {report['commit'][:8]}, HEAD is {head[:8]})"
        parts += [f"## Check report{stale}", "", "```", format_check(report), "```", ""]
    else:
        parts += ["## Check report", "", "(no check has run: run `check` first)", ""]
    if brief.exists():
        try:
            traced = trace(repo, info)
            parts += ["## Acceptance criteria → tests", "", "```", format_trace(traced), "```", ""]
        except ToolError as error:
            parts += ["## Acceptance criteria → tests", "", f"({error})", ""]
    excludes = [":(exclude)plan/", ":(exclude)*.lock", ":(exclude)package-lock.json", ":(exclude)pnpm-lock.yaml"]
    parts += ["## Commits", "", "```", git("log", "--reverse", "--format=%h %s", f"{since}..HEAD").strip(), "```", ""]
    parts += ["## Diff stat", "", "```", git("diff", "--stat", f"{since}...HEAD", "--", ".", *excludes).strip(), "```", ""]
    parts += ["## Diff (against the branch start, plan/ and lockfiles left out)", "", "```diff",
              git("diff", f"{since}...HEAD", "--", ".", *excludes).strip(), "```", ""]
    out = directory / "review-pack.md"
    out.write_text("\n".join(parts))
    return out


def format_trace(traced):
    lines = []
    for ac, tests in traced["covered"].items():
        lines.append(f"{ac}: {len(tests)} test(s)")
        lines += [f"    {t}" for t in tests]
    if traced["uncovered"]:
        lines.append("UNCOVERED: " + ", ".join(traced["uncovered"]))
    if traced["unbound"]:
        lines.append(f"scenarios no step file binds ({traced['bdd']}):")
        lines += [f"    {t}" for t in traced["unbound"]]
    if traced["uncited"]:
        lines.append("tests citing no AC:")
        lines += [f"    {t}" for t in traced["uncited"]]
    lines += traced["unknown"]
    if not traced["acs"]:
        lines.append("the brief's FEATURES files add no @AC-n scenario")
    if not traced["test_files"]:
        lines.append("no changed test files since the branch start")
    return "\n".join(lines)


# --- report (host) -----------------------------------------------------------------

def repo_key(worktree):
    """The main repo's folder name, the same for all its worktrees."""
    common = subprocess.run(["git", "-C", str(worktree), "rev-parse", "--path-format=absolute", "--git-common-dir"],
                            capture_output=True, text=True)
    if common.returncode:
        raise ToolError(f"{worktree}: not a git worktree")
    path = Path(common.stdout.strip())
    return (path.parent if path.name == ".git" else path).name.removesuffix(".git")


def run_name(run):
    meta = json.loads((run / "run.json").read_text())
    return " ".join(p for p in (meta["label"], meta["commit"][:8], meta.get("role") and f"({meta['role']})",
                                Path(meta.get("worktree", "")).name) if p)


def team_report(args):
    """Feed every not yet reported run of the given worktrees into the repo's
    history, then generate the report from the newest run."""
    if os.environ.get("HERDR_SANDBOX"):
        raise ToolError("team-report runs on the host, not inside the sandbox")
    if not ALLURE.exists():
        raise ToolError(f"Allure CLI not found at {ALLURE}; run contrib/local-agents/install.sh")
    worktrees = [Path(a).resolve() for a in args if not a.startswith("--")] or [Path.cwd()]
    keys = {repo_key(w) for w in worktrees}
    if len(keys) > 1:
        raise ToolError(f"worktrees of different repos ({', '.join(sorted(keys))}): one report per repo")
    home = REPORTS / keys.pop()
    home.mkdir(parents=True, exist_ok=True)
    runs = sorted((r for w in worktrees for r in (w / ".team" / "allure").glob("*/") if (r / "run.json").exists()),
                  key=lambda r: (json.loads((r / "run.json").read_text()).get("epoch", 0), r.name))
    if not runs:
        raise ToolError("no runs yet: setup-env, red-check and check leave them in .team/allure/")
    seen_file = home / "processed.txt"
    seen = set(seen_file.read_text().splitlines()) if seen_file.exists() else set()
    new = [r for r in runs if str(r) not in seen]
    history = home / "history.jsonl"
    for run in [r for r in new if r != runs[-1]]:
        result = subprocess.run([str(ALLURE), "history", str(run), "--history-path", str(history),
                                 "--report-name", run_name(run)], capture_output=True, text=True, cwd=home)
        if result.returncode:
            raise ToolError(f"allure history {run}: {result.stderr.strip() or result.stdout.strip()}")
        with seen_file.open("a") as handle:
            handle.write(f"{run}\n")
    latest = runs[-1]
    output = home / "report"
    config = home / "allurerc.mjs"
    config.write_text("export default " + json.dumps({
        "name": run_name(latest), "output": str(output), "historyPath": str(history),
        "appendHistory": str(latest) not in seen,
        "plugins": {"awesome": {"options": {"singleFile": True, "reportName": f"{home.name}: {run_name(latest)}",
                                            "groupBy": ["parentSuite", "suite"]}}}}, indent=2) + ";\n")
    shutil.rmtree(output, ignore_errors=True)
    result = subprocess.run([str(ALLURE), "generate", str(latest), "--config", str(config)],
                            capture_output=True, text=True, cwd=home, env={**os.environ, "NO_UPDATE_NOTIFIER": "1"})
    if result.returncode:
        raise ToolError(f"allure generate: {result.stderr.strip() or result.stdout.strip()}")
    if str(latest) not in seen:
        with seen_file.open("a") as handle:
            handle.write(f"{latest}\n")
    page = next(iter(sorted(output.rglob("index.html"), key=lambda p: len(p.parts))), None)
    print(f"report: {page or output} ({len(new)} new run(s), newest: {run_name(latest)})")
    if page and "--open" in args:
        subprocess.Popen(["xdg-open", str(page)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return 0


# --- entry points ------------------------------------------------------------------

def main(argv):
    name = Path(argv[0]).name
    args = argv[1:]
    if name not in TOOLS:
        if not args or args[0] not in TOOLS:
            print(__doc__)
            return 2
        name, args = args[0], args[1:]
    if args[:1] in (["-h"], ["--help"]):
        print(next(line.strip() for line in __doc__.splitlines() if line.strip().startswith(name + " ")))
        return 0
    if name == "team-report":
        return team_report(args)
    repo = root()
    os.chdir(repo)
    info = facts(repo)

    if name == "repo-facts":
        info["base"] = base(repo)
        print(json.dumps(info, indent=2))
        return 0
    if name == "setup-env":
        if info["setup"]:
            log = team_dir(repo) / "logs" / "setup.log"
            code = run_logged(info["setup"], log, shell=True)
            print(f"setup: {info['setup']} -> {'ok' if code == 0 else 'FAILED'}\nlog: {log}")
            if code:
                print(tail(log, 20))
                return 1
        else:
            print("setup: dependencies present")
        if (repo / ".team" / "known-failures.json").exists() and "--rebaseline" not in args:
            print(f"baseline: already recorded ({len(known_failures(repo))} known failures); --rebaseline to redo")
            return 0
        code, cases, log, failing = record_baseline(repo, info)
        if cases is None:
            print(f"baseline: the suite exits {code}; this runner gives no per-test report, so check can't tell new failures from old\nlog: {log}")
            return 0 if code == 0 else 1
        export_quietly(repo, info, "baseline", cases, set(failing), log)
        print(f"baseline: {sum(1 for c in cases if c['status'] == 'passed')} passed, {len(failing)} already failing"
              + (" (known failures: check reports them but does not block on them)" if failing else ""))
        for failure in failing[:SUMMARY_FAILURES]:
            print(f"  {failure}")
        print(f"log: {log}")
        if not info["bdd"]:
            print("gherkin: no scenario runner in the repo (pytest-bdd or @amiceli/vitest-cucumber); "
                  "tests cite the scenarios' AC-n in comments instead of binding them")
        return 0
    if name == "run-tests":
        code, cases, log = run_tests(repo, info, args)
        print(summarize(code, cases, log))
        return 0 if code == 0 else 1
    if name == "red-check":
        ticket = []
        if (repo / BRIEF).exists():
            ticket = ticket_scenarios(repo, (repo / BRIEF).read_text())[0]
        if not args:
            args = [f for f in changed_files(repo, base(repo)) if matches(f, info["test_patterns"])
                    and not f.endswith(".feature") and Path(f).is_file()]
            if info["bdd"]:
                args += sorted({f for s in ticket for f in scenario_bindings(repo, info, s)} - set(args))
            if not args:
                print("red-check: no new or changed test files since the branch start")
                return 1
        code, cases, log = run_tests(repo, info, args, label="red")
        if cases is None:
            print(f"red-check: this runner gives no per-test report; exit {code}, expected non-zero\nlog: {log}")
            return 0 if code else 1
        export_quietly(repo, info, "red", cases, known_failures(repo), log)
        verdicts = []
        groups, plain = group_cases(cases, scenario_index(repo), info) if info["bdd"] else ({}, cases)
        for case in plain:
            if case["status"] == "passed":
                verdict = "NOT RED: passes already (it does not test new behaviour, or the behaviour exists)"
            elif case["status"] == "skipped":
                verdict = "NOT RED: skipped"
            elif case["kind"] == "assertion":
                verdict = "red ok"
            elif "was not called" in case["message"]:
                verdict = "WRONG REASON: a scenario or step in the feature file has no step code"
            elif case["kind"] == "load":
                verdict = "WRONG REASON: the file does not load; add the module or a stub so the test fails on its assertion"
            else:
                verdict = "WRONG REASON: throws instead of failing an assertion; add a stub that returns a wrong value"
            verdicts.append({**case, "verdict": verdict})
        # A scenario is red when a step fails its assertion and none breaks;
        # scenarios the ticket doesn't add or change are not judged.
        keys = {(s["file"], s["name"]) for s in ticket}
        for key, group in groups.items():
            if key not in keys:
                continue
            failing = next((c for c, _ in group["cases"] if c["status"] == "failed"), None)
            verdict = {"failed": "red ok", "passed": "NOT RED: the scenario passes already",
                       "skipped": "NOT RED: skipped", "unknown": "NOT RED: no result",
                       "broken": "WRONG REASON: a step has no code or throws; add the step code and a stub so a Then step fails its assertion"}[group["status"]]
            verdicts.append({"file": key[0], "name": f"Scenario: {key[1]}", "status": group["status"],
                             "kind": failing["kind"] if failing else None, "message": failing["message"] if failing else "",
                             "location": failing["location"] if failing else "", "verdict": verdict})
        for file, scenario_name in sorted(keys - set(groups)) if info["bdd"] else []:
            verdicts.append({"file": file, "name": f"Scenario: {scenario_name}", "status": "not run", "kind": None,
                             "message": "", "location": "", "verdict": "NOT RED: never ran (no step file in the run binds it, or its file stopped on another error above)"})
        (team_dir(repo) / "red.json").write_text(json.dumps(verdicts, indent=2) + "\n")
        good = [v for v in verdicts if v["verdict"] == "red ok"]
        print(f"red-check: {len(good)}/{len(verdicts)} tests red for the right reason")
        for v in verdicts:
            if v["verdict"] != "red ok":
                print(f"  {v['file']} > {v['name']}: {v['verdict']}" + (f" [{v['message']}]" if v["message"] else ""))
        print(f"evidence: {team_dir(repo) / 'red.json'}\nlog: {log}")
        return 0 if verdicts and len(good) == len(verdicts) else 1
    if name == "ac-trace":
        traced = trace(repo, info)
        print(format_trace(traced))
        return 0 if traced["acs"] and not any(traced[k] for k in ("uncovered", "uncited", "unknown", "unbound")) else 1
    if name == "brief-lint":
        brief = repo / BRIEF
        if not brief.exists():
            print(f"brief-lint: {BRIEF} not found")
            return 1
        problems, questions = lint_brief(repo, brief.read_text())
        for problem in problems:
            print(f"  {problem}")
        if questions:
            print("OPEN QUESTIONS (stop and ask the user):")
            for q in questions:
                print(f"  {q.strip()}")
        if problems:
            print(f"brief-lint: {len(problems)} problem(s)")
            return 1
        print("brief-lint: ok" + (", but there are open questions" if questions else ""))
        return 3 if questions else 0
    if name == "scope-check":
        brief = repo / BRIEF
        owned = owned_paths(brief.read_text()) if brief.exists() else []
        if not owned:
            print("scope-check: the brief lists no OWNED PATHS")
            return 1
        # The planner's feature files are in scope too; check-gaming keeps
        # everyone else from changing them.
        planned = ["plan/", *brief_features(brief.read_text())]
        outside = [f for f in changed_files(repo, base(repo)) if not matches(f, owned + planned)]
        if outside:
            print("scope-check: changed outside OWNED PATHS:")
            print("\n".join(f"  {f}" for f in outside))
            return 1
        print("scope-check: ok")
        return 0
    if name == "check-gaming":
        violations, hints = gaming(repo, info)
        print("check-gaming: " + ("ok" if not violations else "BLOCKING"))
        print("\n".join(f"  {v}" for v in violations))
        if hints:
            print("hints (not blocking):\n" + "\n".join(f"  {h}" for h in hints))
        return 1 if violations else 0
    if name == "check":
        report = check(repo, info, mutation="--mutation" in args)
        print(format_check(report))
        return 0 if report["green"] else 1
    if name == "review-pack":
        out = review_pack(repo, info)
        print(f"review pack: {out} ({len(out.read_text().splitlines())} lines). Read it in full before reviewing.")
        return 0
    return 2


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv))
    except ToolError as error:
        print(f"{Path(sys.argv[0]).name}: {error}", file=sys.stderr)
        sys.exit(2)
