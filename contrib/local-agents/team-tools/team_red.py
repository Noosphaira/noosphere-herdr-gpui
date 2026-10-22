"""red-check's verdicts: which tests the branch added or changed, whether
each fails for the right reason, and whether it goes through an entry point
the brief names.

A test that passes against the stub blocks only when an acceptance
criterion it cites has no failing test: a "not reached" case passes against
a stub that never reaches anything, and that is fine while another test of
the same criterion fails."""

import re

from team_gherkin import TEST_DECL, brief_sections, scenario_bindings, test_declarations
from team_repo import git

HELPER = re.compile(r"^\s*(?:async\s+)?def\s+(?P<py>\w+)"
                    r"|^\s*(?:export\s+)?(?:async\s+)?function\s+(?P<fn>\w+)"
                    r"|^\s*(?:export\s+)?const\s+(?P<arrow>\w+)\s*=\s*(?:async\s*)?(?:\([^)]*\)|\w+)\s*=>")
WRONG_LEVEL = "WRONG LEVEL: never reaches an entry point the brief names ({names}); test what the user does, through it"


def entry_points(text):
    """The brief's ENTRY POINTS: the last part of the first word of each line
    (`agora.domain.room.create_room()` -> create_room)."""
    names = []
    for line in brief_sections(text).get("ENTRY POINTS", "").splitlines():
        word = line.strip().lstrip("-*").strip().split()[:1]
        if word:
            name = re.sub(r"\(.*$", "", word[0].strip("`,")).split(".")[-1].split("::")[-1]
            if name:
                names.append(name)
    return names


def changed_lines(repo, path, since):
    """Line numbers of `path` the branch added or changed; None when the file is new."""
    if not git("ls-tree", since, "--", path).strip():
        return None
    lines = set()
    for hunk in re.finditer(r"^@@ -\S+ \+(\d+)(?:,(\d+))? @@", git("diff", "-U0", since, "--", path), re.M):
        start, count = int(hunk.group(1)), int(hunk.group(2) or 1)
        lines |= set(range(start, start + count))
    return lines


def blocks(lines, starts):
    """{start line: (start, end)} for definitions starting at `starts` (1-based):
    each ends where the indentation comes back to its own (a closing bracket
    there still belongs to it), or before the next definition or test."""
    ordered = sorted(starts)
    spans = {}
    for i, start in enumerate(ordered):
        limit = (ordered[i + 1] - 1) if i + 1 < len(ordered) else len(lines)
        indent = len(lines[start - 1]) - len(lines[start - 1].lstrip())
        end = start
        for number in range(start + 1, limit + 1):
            line = lines[number - 1]
            if not line.strip():
                continue
            if len(line) - len(line.lstrip()) <= indent:
                if line.lstrip()[:1] in "})]":
                    end = number
                break
            end = number
        spans[start] = (start, end)
    return spans


def new_tests(repo, files, since):
    """Tests the branch added or changed, each {file, line, name, acs, reaches}
    where reaches(names) says whether its body, or a helper in its file that
    the body names, mentions one of `names`."""
    found = []
    for file in files:
        path = repo / file
        lines = path.read_text(errors="replace").splitlines()
        declared = test_declarations(path)
        helpers = {}
        for index, line in enumerate(lines):
            match = HELPER.match(line)
            if match and not TEST_DECL.match(line):
                helpers[index + 1] = match.group("py") or match.group("fn") or match.group("arrow")
        spans = blocks(lines, [line for line, _, _ in declared] + list(helpers))
        helper_bodies = {name: "\n".join(lines[spans[start][0] - 1:spans[start][1]]) for start, name in helpers.items()}
        changed = changed_lines(repo, file, since)
        if changed is not None:
            changed = {n for n in changed if n <= len(lines) and lines[n - 1].strip()}
        for line, name, acs in declared:
            first, last = spans[line]
            # The comment lines citing an AC sit right above the declaration.
            above = first
            while above > 1 and re.match(r"^\s*(//|#|\*|/\*|\[|@)", lines[above - 2]):
                above -= 1
            if changed is not None and not changed & set(range(above, last + 1)):
                continue
            body = "\n".join(lines[first - 1:last])
            found.append({"file": file, "line": line, "name": name, "acs": acs,
                          "reaches": _reacher(body, helper_bodies)})
    return found


def _mentions(text, names):
    return any(re.search(rf"\b{re.escape(n)}\b", text) for n in names)


def _reacher(body, helpers):
    def reaches(names):
        return _mentions(body, names) or any(
            _mentions(body, [helper]) and _mentions(text, names) for helper, text in helpers.items())
    return reaches


def module_of(file):
    return re.sub(r"\.\w+$", "", file).replace("/", ".")


def case_of(test, cases):
    """The JUnit cases that ran `test` (parametrised ones give several)."""
    module = module_of(test["file"])
    out = []
    for case in cases:
        if not (case["file"] in (test["file"], module) or case["file"].startswith(module + ".")
                or case["file"].endswith(test["file"])):
            continue
        name = re.sub(r"\[.*\]$", "", case["name"])
        if name == test["name"] or name.endswith(" > " + test["name"]):
            out.append(case)
    return out


def plain_verdict(case):
    if case["status"] == "passed":
        return "passes"
    if case["status"] == "skipped":
        return "NOT RED: skipped"
    if case["kind"] == "assertion":
        return "red ok"
    if "was not called" in case["message"]:
        return "WRONG REASON: a scenario or step in the feature file has no step code"
    if case["kind"] == "load":
        return "WRONG REASON: the file does not load; add the module or a stub so the test fails on its assertion"
    return "WRONG REASON: throws instead of failing an assertion; add a stub that returns a wrong value"


GROUP_VERDICT = {"failed": "red ok", "passed": "passes", "skipped": "NOT RED: skipped", "unknown": "NOT RED: no result",
                 "broken": "WRONG REASON: a step has no code or throws; add the step code and a stub so a Then step fails its assertion"}


def judge(repo, info, cases, groups, ticket, tests, names):
    """(verdicts, problems). `cases` are the plain JUnit cases, `groups` the
    scenario groups, `ticket` the scenarios this branch adds or changes,
    `tests` its new or changed plain tests, `names` the brief's entry points."""
    verdicts, problems = [], []
    if not names:
        problems.append("the brief names no ENTRY POINTS: the planner lists what the task's user calls or runs")
    for test in tests:
        ran = case_of(test, cases)
        base = {"file": test["file"], "name": test["name"], "acs": test["acs"], "kind": None, "message": "", "location": ""}
        if not ran:
            verdicts.append({**base, "status": "not run", "verdict": "NOT RED: never ran (its file stopped on another error?)"})
            continue
        for case in ran:
            verdict = plain_verdict(case)
            if names and not test["reaches"](names) and not verdict.startswith("WRONG REASON"):
                verdict = WRONG_LEVEL.format(names=", ".join(names))
            verdicts.append({**case, "name": case["name"], "acs": test["acs"], "verdict": verdict})
    keys = {(s["file"], s["name"]): s for s in ticket}
    for key, scenario in keys.items():
        group = groups.get(key)
        base = {"file": key[0], "name": f"Scenario: {key[1]}", "acs": scenario["acs"]}
        if group is None:
            if info["bdd"]:
                verdicts.append({**base, "status": "not run", "kind": None, "message": "", "location": "",
                                 "verdict": "NOT RED: never ran (no step file in the run binds it, or its file stopped on another error above)"})
            continue
        failing = next((c for c, _ in group["cases"] if c["status"] == "failed"), None)
        verdict = GROUP_VERDICT[group["status"]]
        bound = scenario_bindings(repo, info, scenario)
        if names and bound and not verdict.startswith("WRONG REASON") and \
                not any(_mentions((repo / f).read_text(errors="replace"), names) for f in bound):
            verdict = WRONG_LEVEL.format(names=", ".join(names))
        verdicts.append({**base, "status": group["status"], "kind": failing["kind"] if failing else None,
                         "message": failing["message"] if failing else "",
                         "location": failing["location"] if failing else "", "verdict": verdict})
    red_acs = {ac for v in verdicts if v["verdict"] == "red ok" for ac in v["acs"]}
    for v in verdicts:
        if v["verdict"] != "passes":
            continue
        missing = [ac for ac in v["acs"] if ac not in red_acs]
        if v["acs"] and not missing:
            v["verdict"] = "passes against the stub (allowed: " + ", ".join(f"AC-{a}" for a in v["acs"]) + " has a failing test)"
        else:
            v["verdict"] = ("NOT RED: passes already, and " + (", ".join(f"AC-{a}" for a in missing) + " has no failing test"
                            if v["acs"] else "it cites no AC") + " (it does not test new behaviour, or the behaviour exists)")
    for ac in sorted({ac for s in ticket for ac in s["acs"]} - red_acs):
        problems.append(f"AC-{ac}: no test or scenario fails for it")
    return verdicts, problems


def blocking(verdict):
    return verdict["verdict"] != "red ok" and not verdict["verdict"].startswith("passes against the stub")
