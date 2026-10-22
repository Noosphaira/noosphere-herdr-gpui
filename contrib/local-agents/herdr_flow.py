"""A team's stages, and the state machine that moves work between them.

The team file names the stages; nothing here knows any role:

  start: plan
  stages:
    plan:   {role: planner, next: red}
    red:    {role: test-author, check: red-check, pass: green, fail: red}
    green:  {role: tdd-coder, check: check, pass: review, fail: green}
    review: {role: tdd-reviewer, next: finish, findings: green}
  limits: {fail: 3, findings: 3}

A stage belongs to one role. When that role reports `team done`, a stage
without a check moves to `next`; a stage with one has the check run (by the
orchestrator, in the sandbox, with the role's summary in $TEAM_REPORT) and
moves to `pass` or `fail`. `team findings`
moves to `findings`, where the stage allows it. `finish` ends the team. More
than `limits.fail` failed checks in one stage, or more than `limits.findings`
findings in all, stop the team and notify the user.

A team file without `stages` gets one stage per `flow` entry, each moving
to the next, the last allowing findings back to the one before it
(`review_rounds` times), as teams worked before stages existed.
"""

FINISH = "finish"
DEFAULT_LIMITS = {"fail": 3, "findings": 3}
STAGE_KEYS = {"role", "check", "next", "pass", "fail", "findings"}
# What a role sees of a failed check: the end of its output.
CHECK_TAIL = 60


class FlowError(Exception):
    pass


def parse(team, roles):
    """{start, stages, limits} from a team file's data, validated."""
    if "stages" in team:
        stages = team["stages"]
        start = team.get("start") or (next(iter(stages)) if isinstance(stages, dict) and stages else None)
    else:
        stages, start = derived(team, roles)
    if not isinstance(stages, dict) or not stages:
        raise FlowError("stages must map stage names to stages")
    for name, stage in stages.items():
        where = f"stage {name}"
        if name == FINISH:
            raise FlowError(f"{FINISH!r} is reserved: it ends the team")
        if not isinstance(stage, dict) or set(stage) - STAGE_KEYS:
            raise FlowError(f"{where}: keys are {', '.join(sorted(STAGE_KEYS))}")
        if stage.get("role") not in roles:
            raise FlowError(f"{where}: role must be one of {', '.join(roles)}")
        if "check" in stage:
            if not isinstance(stage["check"], str) or not stage["check"].strip():
                raise FlowError(f"{where}: check must be a command")
            if "next" in stage or not {"pass", "fail"} <= set(stage):
                raise FlowError(f"{where}: a stage with a check names pass and fail, not next")
        elif "next" not in stage or {"pass", "fail"} & set(stage):
            raise FlowError(f"{where}: a stage without a check names next")
        for key in ("next", "pass", "fail", "findings"):
            if key in stage and stage[key] != FINISH and stage[key] not in stages:
                raise FlowError(f"{where}: {key} must be a stage or {FINISH}")
    if start not in stages:
        raise FlowError("start must be a stage")
    limits = {**DEFAULT_LIMITS, **(team.get("limits") or {})}
    if "stages" not in team and team.get("review_rounds") is not None and "findings" not in (team.get("limits") or {}):
        limits["findings"] = team["review_rounds"]
    if set(limits) != set(DEFAULT_LIMITS) or not all(isinstance(v, int) and 0 <= v <= 10 for v in limits.values()):
        raise FlowError("limits takes fail and findings, each a number from 0 to 10")
    return {"start": start, "stages": stages, "limits": limits}


def derived(team, roles):
    flow = team.get("flow", roles)
    if not isinstance(flow, list) or sorted(flow) != sorted(roles):
        raise FlowError("flow must list each role exactly once")
    stages = {}
    for index, role in enumerate(flow):
        stage = {"role": role, "next": flow[index + 1] if index + 1 < len(flow) else FINISH}
        if index == len(flow) - 1 and index > 0:
            stage["findings"] = flow[index - 1]
        stages[role] = stage
    return stages, flow[0]


class Machine:
    """Decides moves; the orchestrator keeps the state and carries them out.

    A move is {to, role, message} (`to` is a stage or FINISH) or, when a
    limit is reached, {stop: reason}."""

    def __init__(self, flow, task):
        self.flow = flow
        self.stages = flow["stages"]
        self.task = task

    def role(self, stage):
        return self.stages[stage]["role"]

    def first(self):
        stage = self.flow["start"]
        return self.enter(stage, f"Task:\n{self.task}")

    def done(self, state, summary):
        """The stage's role reported done. Returns a move, or a check to run."""
        stage = self.stages[state["stage"]]
        if "check" in stage:
            return {"check": stage["check"]}
        who = stage["role"]
        return self.go(state, stage["next"], f"{who} finished stage {state['stage']}: {summary}")

    def checked(self, state, summary, passed, output):
        stage = self.stages[state["stage"]]
        who, command = stage["role"], stage["check"]
        if passed:
            return self.go(state, stage["pass"], f"{who} finished stage {state['stage']} and `{command}` passed: {summary}")
        fails = state["counts"].get(f"fail:{state['stage']}", 0) + 1
        if fails > self.flow["limits"]["fail"]:
            return {"stop": f"`{command}` failed {fails} times in stage {state['stage']}"}
        state["counts"][f"fail:{state['stage']}"] = fails
        tail = "\n".join(output.splitlines()[-CHECK_TAIL:])
        return self.go(state, stage["fail"], f"`{command}` failed after {who}'s `team done` in stage "
                                             f"{state['stage']}. Its output ends:\n{tail}")

    def findings(self, state, text):
        stage = self.stages[state["stage"]]
        rounds = state["counts"].get("findings", 0) + 1
        if rounds > self.flow["limits"]["findings"]:
            return {"stop": f"findings were sent {rounds} times; the limit is {self.flow['limits']['findings']}"}
        state["counts"]["findings"] = rounds
        return self.go(state, stage["findings"], f"{stage['role']} sent findings (round {rounds}):\n{text}")

    def go(self, state, target, origin):
        if target == FINISH:
            return {"to": FINISH, "role": None, "message": origin}
        return self.enter(target, origin)

    def enter(self, stage, origin):
        role = self.role(stage)
        spec = self.stages[stage]
        lines = [f"[team] Your turn: stage {stage}.", origin, ""]
        if "check" in spec:
            lines.append(f"When your part is done and committed, run `team done \"<summary>\"`: the team then "
                         f"runs `{spec['check']}` and moves the work on only if it passes.")
        else:
            lines.append("When your part is done and committed, run `team done \"<summary>\"`.")
        if "findings" in spec:
            lines.append(f"If there is something to fix, run `team findings \"<numbered findings>\"` instead: "
                         f"they go to {self.role(spec['findings']) if spec['findings'] != FINISH else 'nobody'}.")
        lines.append("If you are blocked (a tool can't run), run `team blocked \"<what failed>\"` and stop. "
                     "To ask a teammate something, `team ask <role> \"<question>\"`; that does not end your turn.")
        return {"to": stage, "role": role, "message": "\n".join(lines)}

    def allows_findings(self, stage):
        return "findings" in self.stages[stage]
