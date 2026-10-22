"""Acceptance criteria: the brief, its Gherkin scenarios, the step code and
tests that bind or cite them, and the trace between them."""

import json
import re
import shlex
import shutil
from pathlib import Path

from team_repo import BRIEF, ToolError, base, changed_files, git, matches

SECTIONS = ("INTENT", "FEATURES", "ENTRY POINTS", "GUARDRAILS", "OWNED PATHS", "TEST COMMAND",
            "CONTEXT POINTERS")
MAX_ACS = 8
TEST_DECL = re.compile(
    r"""^\s*(?:it|test)(?:\.each\([^)]*\))?\s*\(\s*(['"`])(?P<js>.+?)\1"""
    r"""|^\s*(?:async\s+)?def\s+(?P<py>test_\w+)"""
    r"""|^\s*public\s+(?:async\s+)?\w+\s+(?P<cs>\w+)\s*\("""
)
AC_REF = re.compile(r"\bAC-(\d+)\b")


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
    """Problems that block the hand-off. The team works on its own, so an
    open question is one too: the planner decides it and records it under
    DECISIONS."""
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
    for question in questions.splitlines():
        if question.strip() and question.strip().lstrip("-* ").lower() not in ("none", "n/a", ""):
            problems.append(f"open question {question.strip().lstrip('-* ')!r}: nobody answers questions; "
                            "decide it from the repo's code and docs and record it under DECISIONS")
    return problems


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
