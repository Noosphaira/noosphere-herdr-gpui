"""Allure results from the tools' runs (in the sandbox) and the HTML report
with history built from them (on the host only)."""

import hashlib
import json
import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path

from team_gherkin import (case_status, group_cases, last_results, scenario_index, test_declarations,
                          ticket_scenarios)
from team_repo import BRIEF, ToolError, case_id, git, team_dir

REPORTS = Path(os.environ.get("TEAM_REPORTS", "~/.local/share/herdr-launch/reports")).expanduser()
ALLURE = Path(os.environ.get("TEAM_ALLURE", "~/.local/share/herdr-launch/allure-cli/node_modules/.bin/allure")).expanduser()


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
