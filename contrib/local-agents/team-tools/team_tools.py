#!/usr/bin/env python3
"""Deterministic tools for team roles: everything with a checkable answer.

One file, called by the name it is linked as (or `team_tools.py <tool>`):

  repo-facts     the repo's stack, commands and test-file patterns, as JSON
  setup-env      install dependencies when missing (npm ci, ...), then record
                 which tests already fail at the branch start
  run-tests      run tests, print a short summary; the full log goes to a file
  red-check      the tests and scenarios the branch adds or changes fail on an
                 assertion (not a load/type error or a step with no code), go
                 through an entry point the brief names, and every criterion
                 has one failing; one passing against the stub is allowed when
                 another test of its criterion fails
  ac-trace       every acceptance criterion (an @AC-n scenario) is bound to step
                 code or cited by a test; every new test cites one
  brief-lint     plan/brief.md has the sections and limits the planner owes, and
                 its feature files are well-formed Gherkin
  scope-check    changed files stay inside the brief's OWNED PATHS
  check-gaming   no test edits after RED, no feature edits outside "plan:"
                 commits, no new skips, no deleted tests
  check          the gate: tests, typecheck, lint, check-gaming (+ mutation)
  review-check   every OWNED PATH the branch never changed is named, with why,
                 in the reviewer's report ($TEAM_REPORT, set by the team)
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
the tool could not run.
"""

import json
import os
import re
import shlex
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

# The tools' modules sit beside this file; the tool names are links to it.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import team_allure  # noqa: E402,F401  (its paths are module settings)
import team_red  # noqa: E402
from team_allure import export_quietly, team_report  # noqa: E402,F401
from team_gherkin import (  # noqa: E402,F401
    BRIEF, TEST_DECL, brief_features, group_cases, last_results, lint_brief, owned_paths, parse_feature,
    python_name, scenario_bindings, scenario_index, step_kinds, test_declarations, ticket_scenarios, trace,
    format_trace)
from team_repo import (  # noqa: E402,F401
    ToolError, base, case_id, changed_files, git, matches, root, team_dir)

TOOLS = ("repo-facts", "setup-env", "run-tests", "red-check", "ac-trace", "brief-lint",
         "scope-check", "check-gaming", "check", "review-check", "review-pack", "team-report")

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
SNAPSHOT = re.compile(r"(__snapshots__/|\.snap$|\.verified\.)")
ASSERTION_TYPES = ("AssertionError", "AssertError", "AssertionFailedError", "XunitException")


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
        python, setup = python_env(repo)
        commands = {"test": f"{shlex.quote(python)} -m pytest -q"}
        pyproject = (repo / "pyproject.toml").read_text() if (repo / "pyproject.toml").exists() else ""
        if "[tool.ruff" in pyproject:
            commands["lint"] = "ruff check ."
        if "[tool.mypy" in pyproject:
            commands["typecheck"] = "mypy ."
        if "mutmut" in pyproject:
            commands["mutation"] = "mutmut run"
        declared = pyproject + "".join(p.read_text(errors="replace") for p in repo.glob("requirements*.txt"))
        bdd = "pytest-bdd" if re.search(r"pytest[-_]bdd", declared) else None
        info.update(stack="python", runner="pytest", commands=commands, test_patterns=TEST_PATTERNS["python"], bdd=bdd,
                    python=python, package_manager="uv" if (repo / "uv.lock").exists() else None, setup=setup)
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


def python_env(repo):
    """(interpreter, setup command or None): the repo's own virtualenv when it
    has one or a uv.lock makes one; the system Python otherwise."""
    venv = repo / ".venv" / "bin" / "python"
    if (repo / "uv.lock").exists():
        return str(venv), None if venv.exists() else "uv sync --locked"
    return (str(venv), None) if venv.exists() else (sys.executable, None)


def runner_problem(info):
    """Why the test runner can't start here, or None."""
    if info["runner"] == "pytest":
        python = info.get("python") or sys.executable
        if not Path(python).exists():
            return f"{python} does not exist (run setup-env)"
        probe = subprocess.run([python, "-c", "import pytest"], capture_output=True, text=True)
        if probe.returncode:
            return f"pytest is not installed for {python}"
    elif info["stack"] == "node" and info["setup"]:
        return "node_modules is missing (run setup-env)"
    return None


RUNNER_BROKEN = ("Do not work around this (no other interpreter, no hand-made reports): "
                 "the team's checks need this tool. If setup-env can't fix it, run "
                 "`team wait \"<what failed>\"` and stop.")


# --- running tests ---------------------------------------------------------------

def test_command(info, files, junit):
    """Command that runs `files` (all when empty) and writes JUnit XML to
    `junit`, or None when the runner can't produce JUnit (then plain run)."""
    if info["runner"] == "vitest":
        prefix = {"npm": ["npx"], "pnpm": ["pnpm", "exec"], "yarn": ["yarn"], "bun": ["bunx"]}[info["package_manager"]]
        return prefix + ["vitest", "run", *files, "--reporter=dot", "--reporter=junit",
                         f"--outputFile.junit={junit}"]
    if info["runner"] == "pytest":
        return [info.get("python") or sys.executable, "-m", "pytest", "-q", f"--junitxml={junit}", *files]
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
    problem = runner_problem(info)
    if problem:
        raise ToolError(f"the test runner can't start: {problem}. {RUNNER_BROKEN}")
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


# --- result files ----------------------------------------------------------------

def evidence_problems(repo):
    """A red.json that says "red ok" for passing tests was not written by red-check."""
    red = repo / ".team" / "red.json"
    if not red.exists():
        return []
    try:
        verdicts = json.loads(red.read_text())
    except ValueError:
        return [".team/red.json is not valid JSON: only red-check writes it"]
    if any(isinstance(v, dict) and v.get("verdict") == "red ok" and v.get("status") == "passed" for v in verdicts):
        return [".team/red.json says \"red ok\" for passing tests: it was not written by red-check"]
    return []


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
    violations += evidence_problems(repo)
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


# --- check -------------------------------------------------------------------------

def check(repo, info, mutation=False):
    directory = team_dir(repo)
    report = {"time": time.strftime("%Y-%m-%dT%H:%M:%S"), "commit": git("rev-parse", "HEAD").strip(),
              "dirty": bool(git("status", "--porcelain").strip()), "steps": {}}
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


# --- review -------------------------------------------------------------------------

def untouched(repo, text):
    """The brief's OWNED PATHS no change since the branch start falls under."""
    changed = changed_files(repo, base(repo))
    return [path for path in owned_paths(text) if not any(matches(f, [path]) for f in changed)]


def unexplained(paths, report):
    """The untouched paths the report does not name."""
    return [path for path in paths if path not in report]


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
        text = (repo / BRIEF).read_text() if (repo / BRIEF).exists() else ""
        ticket = ticket_scenarios(repo, text)[0] if text else []
        since = base(repo)
        files = [f for f in changed_files(repo, since) if matches(f, info["test_patterns"])
                 and not f.endswith(".feature") and Path(f).is_file()]
        if not args:
            args = list(files)
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
        groups, plain = group_cases(cases, scenario_index(repo), info) if info["bdd"] else ({}, cases)
        # Only what the branch adds or changes is judged, never a file's old tests.
        tests = team_red.new_tests(repo, [f for f in files if f in args], since)
        verdicts, problems = team_red.judge(repo, info, plain, groups, ticket, tests, team_red.entry_points(text))
        (team_dir(repo) / "red.json").write_text(json.dumps(verdicts, indent=2) + "\n")
        good = [v for v in verdicts if v["verdict"] == "red ok"]
        bad = [v for v in verdicts if team_red.blocking(v)]
        print(f"red-check: {len(good)}/{len(verdicts)} new or changed tests red for the right reason")
        for v in bad:
            print(f"  {v['file']} > {v['name']}: {v['verdict']}" + (f" [{v['message']}]" if v["message"] else ""))
        for problem in problems:
            print(f"  {problem}")
        allowed = [v for v in verdicts if v not in bad and v["verdict"] != "red ok"]
        if allowed:
            print("not blocking:")
            print("\n".join(f"  {v['file']} > {v['name']}: {v['verdict']}" for v in allowed))
        print(f"evidence: {team_dir(repo) / 'red.json'}\nlog: {log}")
        return 0 if good and not bad and not problems else 1
    if name == "ac-trace":
        traced = trace(repo, info)
        print(format_trace(traced))
        return 0 if traced["acs"] and not any(traced[k] for k in ("uncovered", "uncited", "unknown", "unbound")) else 1
    if name == "brief-lint":
        brief = repo / BRIEF
        if not brief.exists():
            print(f"brief-lint: {BRIEF} not found")
            return 1
        problems = lint_brief(repo, brief.read_text())
        for problem in problems:
            print(f"  {problem}")
        if problems:
            print(f"brief-lint: {len(problems)} problem(s)")
            return 1
        print("brief-lint: ok")
        return 0
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
    if name == "review-check":
        brief = repo / BRIEF
        if not brief.exists():
            print(f"review-check: {BRIEF} not found")
            return 1
        missing = unexplained(untouched(repo, brief.read_text()), os.environ.get("TEAM_REPORT", ""))
        if missing:
            print("review-check: the brief's OWNED PATHS below were never changed, and the report does not "
                  "say why. Each is either work the brief planned and nobody did (send it as findings) or "
                  "not needed after all (name the path in your `team done` summary with the reason):")
            print("\n".join(f"  {path}" for path in missing))
            return 1
        print("review-check: ok")
        return 0
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
