#!/usr/bin/env python3
"""Deterministic tools for team roles: everything with a checkable answer.

One file, called by the name it is linked as (or `team_tools.py <tool>`):

  repo-facts     the repo's stack, commands and test-file patterns, as JSON
  setup-env      install dependencies when missing (npm ci, ...), then record
                 which tests already fail at the branch start
  run-tests      run tests, print a short summary; the full log goes to a file
  red-check      new tests must fail on an assertion, not on a load/type error
  ac-trace       every acceptance criterion has a test; every new test cites one
  brief-lint     plan/brief.md has the sections and limits the planner owes
  scope-check    changed files stay inside the brief's OWNED PATHS
  check-gaming   no test edits after RED, no new skips, no deleted tests
  check          the gate: tests, typecheck, lint, check-gaming (+ mutation)
  review-pack    one file for the reviewer: brief, diff, check report, ac-trace

Tests that already fail when setup-env runs (say, ones needing a database the
sandbox can't reach) are saved as known failures: check reports them but blocks
only on new ones. Reports and logs go to .team/ in the worktree (ignored by git through
info/exclude). Commit subjects carry the TDD state: "red: ...", "green: ...",
"refactor: ...". Exit status 0 means pass; 1 means the check failed; 2 means
the tool could not run; 3 (brief-lint) means the brief has open questions.
"""

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

TOOLS = ("repo-facts", "setup-env", "run-tests", "red-check", "ac-trace", "brief-lint",
         "scope-check", "check-gaming", "check", "review-pack")

BRIEF = Path("plan/brief.md")
SECTIONS = ("INTENT", "DONE WHEN", "GUARDRAILS", "OWNED PATHS", "TEST COMMAND", "CONTEXT POINTERS")
MAX_ACS = 8
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
    return sorted(n for n in names if not n.startswith(".team/"))


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
    info = {"root": str(repo), "stack": "generic", "package_manager": None, "runner": None,
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
        info.update(stack="python", runner="pytest", commands=commands, test_patterns=TEST_PATTERNS["python"])
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
        entry = {"file": file, "name": name, "status": "passed", "kind": None, "message": "", "location": ""}
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
            entry.update(status="failed", kind=kind, message=f"{kind_name}: {message}".strip(": ").splitlines()[0][:200],
                         location=(located.group(1) or located.group(2)) if located else "")
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


def brief_acs(text):
    """AC numbers defined in DONE WHEN, in order."""
    body = brief_sections(text).get("DONE WHEN", "")
    return [int(m.group(1)) for m in re.finditer(r"^\s*(?:[-*]|\d+\.)?\s*\**AC-(\d+)\b", body, re.M)]


def owned_paths(text):
    body = brief_sections(text).get("OWNED PATHS", "")
    paths = []
    for line in body.splitlines():
        line = line.strip().lstrip("-*").strip()
        if line:
            paths.append(line.split()[0].strip("`,"))
    return paths


def lint_brief(repo, text):
    """(problems, questions): problems block the hand-off; questions mean the
    planner must stop and ask."""
    problems = []
    sections = brief_sections(text)
    for name in SECTIONS:
        if not sections.get(name):
            problems.append(f"missing or empty section: {name}")
    acs = brief_acs(text)
    if not acs and sections.get("DONE WHEN"):
        problems.append("DONE WHEN has no AC-1, AC-2, ... lines")
    if len(acs) > MAX_ACS:
        problems.append(f"{len(acs)} acceptance criteria; at most {MAX_ACS} (split the task)")
    if acs and acs != list(range(1, len(acs) + 1)):
        problems.append(f"acceptance criteria must be numbered AC-1..AC-{len(acs)} in order, found {acs}")
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


def trace(repo, info):
    brief = repo / BRIEF
    if not brief.exists():
        raise ToolError(f"{BRIEF} not found")
    acs = brief_acs(brief.read_text())
    since = base(repo)
    tests = [f for f in changed_files(repo, since) if matches(f, info["test_patterns"]) and (repo / f).is_file()]
    covered = {ac: [] for ac in acs}
    uncited, unknown = [], []
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
            "uncited": uncited, "unknown": unknown, "test_files": tests}


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
    if red:
        after = commits[red[-1] + 1:]
        for sha, subject in after:
            touched = git("diff-tree", "--no-commit-id", "--name-only", "-r", sha).split()
            tests = [t for t in touched if matches(t, info["test_patterns"])]
            if tests:
                violations.append(f"{sha[:8]} \"{subject}\" changes tests after RED: {', '.join(tests)}")
        dirty = [f for f in git("diff", "--name-only", "HEAD").split() if matches(f, info["test_patterns"])]
        if dirty and not commits[-1][1].lower().startswith("red:"):
            violations.append(f"uncommitted test changes after RED: {', '.join(dirty)}")
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
            elif not current.startswith("plan/") and not current.endswith((".md", ".txt")):
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
              "dirty": bool(git("status", "--porcelain", "--untracked-files=no").strip()), "steps": {}}
    code, cases, log = run_tests(repo, info, [], label="check-tests")
    known = known_failures(repo)
    passed = sum(1 for c in cases if c["status"] == "passed") if cases else None
    report["steps"]["tests"] = {"ok": tests_ok(code, cases, known), "summary": summarize(code, cases, log, known), "passed": passed}
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
    if traced["uncited"]:
        lines.append("tests citing no AC:")
        lines += [f"    {t}" for t in traced["uncited"]]
    lines += traced["unknown"]
    if not traced["test_files"]:
        lines.append("no changed test files since the branch start")
    return "\n".join(lines)


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
        print(f"baseline: {sum(1 for c in cases if c['status'] == 'passed')} passed, {len(failing)} already failing"
              + (" (known failures: check reports them but does not block on them)" if failing else ""))
        for failure in failing[:SUMMARY_FAILURES]:
            print(f"  {failure}")
        print(f"log: {log}")
        return 0
    if name == "run-tests":
        code, cases, log = run_tests(repo, info, args)
        print(summarize(code, cases, log))
        return 0 if code == 0 else 1
    if name == "red-check":
        if not args:
            args = [f for f in changed_files(repo, base(repo)) if matches(f, info["test_patterns"]) and Path(f).is_file()]
            if not args:
                print("red-check: no new or changed test files since the branch start")
                return 1
        code, cases, log = run_tests(repo, info, args, label="red")
        if cases is None:
            print(f"red-check: this runner gives no per-test report; exit {code}, expected non-zero\nlog: {log}")
            return 0 if code else 1
        verdicts = []
        for case in cases:
            if case["status"] == "passed":
                verdict = "NOT RED: passes already (it does not test new behaviour, or the behaviour exists)"
            elif case["status"] == "skipped":
                verdict = "NOT RED: skipped"
            elif case["kind"] == "assertion":
                verdict = "red ok"
            elif case["kind"] == "load":
                verdict = "WRONG REASON: the file does not load; add the module or a stub so the test fails on its assertion"
            else:
                verdict = "WRONG REASON: throws instead of failing an assertion; add a stub that returns a wrong value"
            verdicts.append({**case, "verdict": verdict})
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
        return 0 if not traced["uncovered"] and not traced["uncited"] and not traced["unknown"] else 1
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
        outside = [f for f in changed_files(repo, base(repo)) if not matches(f, owned + ["plan/"])]
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
