"""The only route from a sandboxed agent to herdr.

herdr's control socket can run commands in any pane, so it is never bound
into a sandbox. Each sandbox gets this filter's socket instead, which allows:

- the agent-state reports OpenCode's herdr integration sends, for the
  agent's own pane only;
- with a team, the `team.*` methods the `team` command uses: read the task
  and the stages, see teammates' status, report the turn's outcome (done,
  findings, blocked), and ask or answer a teammate.

An agent never hands work on itself. A report goes into the team's state,
where the orchestrator (herdr-team-watch, outside every sandbox) runs the
stage's check and moves the work to the stage the team file names. Only the
role whose turn it is can report. Questions and answers become herdr's
`agent.prompt` to a teammate's pane, which herdr only delivers to a
recognised agent, never to a bare shell; the sender is set here, not by the
agent, and they never change whose turn it is.
"""

import json
import os
import socket
import threading
import time

import herdr_team

MAX_QUESTION = 1000
REPORT_METHODS = {"pane.report_agent", "pane.report_agent_session", "pane.release_agent"}
MAX_REQUEST = 64 * 1024


class Filter:
    def __init__(self, upstream, pane_id, team_dir=None, role=None):
        self.upstream = upstream
        self.pane_id = pane_id
        self.team_dir = team_dir
        self.role = role
        self.team = herdr_team.load(team_dir) if team_dir else None

    # --- herdr ------------------------------------------------------------

    def forward(self, line):
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as up:
            up.settimeout(10)
            up.connect(self.upstream)
            up.sendall(line)
            data = b""
            while not data.endswith(b"\n") and len(data) < MAX_REQUEST:
                chunk = up.recv(4096)
                if not chunk:
                    break
                data += chunk
            return data

    def herdr(self, method, params):
        """Call herdr; returns (result, error)."""
        line = json.dumps({"id": f"herdr-team:{method}", "method": method, "params": params})
        answer = json.loads(self.forward(line.encode() + b"\n") or b"{}")
        return answer.get("result"), answer.get("error")

    # --- team methods -----------------------------------------------------

    def current_role(self, current):
        stage = current.get("stage")
        return self.team["flow"]["stages"][stage]["role"] if stage else None

    def team_info(self, _params):
        current = herdr_team.read_state(self.team_dir)
        flow = self.team["flow"]
        mine = [name for name, stage in flow["stages"].items() if stage["role"] == self.role]
        return {
            "role": self.role,
            "task": self.team["task"],
            "branch": self.team["branch"],
            "roles": list(self.team["members"]),
            "stage": current.get("stage"),
            "turn": self.current_role(current),
            "your_stages": {name: flow["stages"][name] for name in mine},
            "done": current["done"],
            "stopped": current.get("stopped"),
        }

    def team_status(self, _params):
        statuses = {}
        for role, pane in self.team["members"].items():
            result, error = self.herdr("agent.get", {"target": pane})
            if error:
                statuses[role] = "gone"
            else:
                agent = result.get("agent") or result.get("pane") or {}
                statuses[role] = agent.get("agent_status", "unknown")
        return {"statuses": statuses, **self.team_info(None)}

    def report(self, kind, text):
        """Queue the turn's outcome for the orchestrator, which runs the
        stage's check and moves the work on."""
        stages = self.team["flow"]["stages"]
        with herdr_team.state(self.team_dir) as current:
            if current["done"] or current.get("stopped"):
                raise TeamError("finished", "the team has finished")
            stage = current.get("stage")
            if self.current_role(current) != self.role:
                raise TeamError("not_your_turn", f"it is {self.current_role(current) or 'nobody'}'s turn (stage {stage}); "
                                                 "to talk to a teammate, use `team ask <role> ...`")
            if current["events"]:
                raise TeamError("pending", "your last report is still being handled; wait for the team's message")
            if kind == "findings" and "findings" not in stages[stage]:
                raise TeamError("invalid", f"stage {stage} takes no findings; report with `team done`")
            current["events"].append({"kind": kind, "role": self.role, "stage": stage, "text": text, "time": time.time()})
        check = stages[stage].get("check")
        if kind == "done" and check:
            return {"reported": kind, "next": f"the team runs `{check}` now and messages you if it fails; stop"}
        return {"reported": kind, "next": "the team hands the work on; stop"}

    def team_done(self, params):
        return self.report("done", herdr_team.clean(params.get("summary", ""), herdr_team.MAX_MESSAGE).strip())

    def team_findings(self, params):
        text = herdr_team.clean(params.get("text", ""), herdr_team.MAX_MESSAGE).strip()
        if not text:
            raise TeamError("invalid", "list the findings")
        return self.report("findings", text)

    def team_blocked(self, params):
        what = herdr_team.clean(params.get("text", ""), MAX_QUESTION).strip()
        if not what:
            raise TeamError("invalid", "say what blocks you")
        with herdr_team.state(self.team_dir) as current:
            if current["done"] or current.get("stopped"):
                raise TeamError("finished", "the team has finished")
            current["waiting"] = {"role": self.role, "question": what, "time": time.time()}
            current["log"].append({"time": time.time(), "from": self.role, "blocked": True, "text": what[:200]})
        herdr_team.notify(f"Team {self.team['branch']}: {self.role} is blocked", what)
        self.report_waiting(True)
        return {"blocked": True}

    def report_waiting(self, waiting, role=None):
        """Mark a role's pane for the GUI (a display-only metadata token); best effort."""
        params = {"pane_id": self.team["members"][role or self.role], "source": herdr_team.WAITING_SOURCE,
                  "tokens": {herdr_team.WAITING_TOKEN: "1" if waiting else None}}
        try:
            self.herdr("pane.report_metadata", params)
        except (OSError, ValueError):
            pass

    def deliver(self, to, text):
        if to not in self.team["members"]:
            raise TeamError("unknown_role", f"no teammate {to!r}; roles are {', '.join(self.team['members'])}")
        if to == self.role:
            raise TeamError("invalid", "you cannot message yourself")
        if not text:
            raise TeamError("invalid", "the message is empty")
        if herdr_team.read_state(self.team_dir)["done"]:
            raise TeamError("finished", "the team has finished")
        _, error = self.herdr("agent.prompt", {"target": self.team["members"][to], "text": text})
        if error:
            raise TeamError(error.get("code", "herdr_error"), error.get("message", "herdr refused"))

    def team_ask(self, params):
        to = params.get("to")
        text = herdr_team.clean(params.get("text", ""), herdr_team.MAX_MESSAGE).strip()
        self.deliver(to, f"[team question from {self.role}] {text}\n(Answer with `team answer {self.role} \"...\"`. "
                         "This does not change whose turn it is.)" if text else "")
        herdr_team.log(self.team_dir, {"from": self.role, "ask": to, "text": text[:200]})
        return {"asked": to}

    def team_answer(self, params):
        to = params.get("to")
        text = herdr_team.clean(params.get("text", ""), herdr_team.MAX_MESSAGE).strip()
        self.deliver(to, f"[team answer from {self.role}] {text}" if text else "")
        herdr_team.log(self.team_dir, {"from": self.role, "answer": to, "text": text[:200]})
        return {"answered": to}

    TEAM_METHODS = {
        "team.info": team_info,
        "team.status": team_status,
        "team.done": team_done,
        "team.findings": team_findings,
        "team.blocked": team_blocked,
        "team.ask": team_ask,
        "team.answer": team_answer,
    }

    # --- requests ---------------------------------------------------------

    def handle(self, line):
        """Answer one request line; returns the reply bytes."""
        try:
            request = json.loads(line)
            request_id = request.get("id")
            method = request.get("method")
            params = request.get("params") or {}
            if not isinstance(params, dict):
                raise ValueError
        except (ValueError, AttributeError):
            return error_reply(None, "invalid_request", "not a JSON object")
        if method in REPORT_METHODS:
            if params.get("pane_id") != self.pane_id:
                return error_reply(request_id, "forbidden", "agents may only report their own pane")
            return self.forward(line + b"\n")
        if method in self.TEAM_METHODS and self.team:
            try:
                result = self.TEAM_METHODS[method](self, params)
            except TeamError as error:
                return error_reply(request_id, error.code, error.message)
            return (json.dumps({"id": request_id, "result": result}) + "\n").encode()
        return error_reply(request_id, "forbidden", f"{method} is not allowed from the sandbox")

    def serve(self, conn):
        with conn:
            conn.settimeout(30)
            buffer = b""
            while True:
                try:
                    chunk = conn.recv(4096)
                except OSError:
                    return
                if not chunk:
                    return
                buffer += chunk
                if len(buffer) > MAX_REQUEST:
                    return
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    try:
                        conn.sendall(self.handle(line) or b"")
                    except OSError as error:
                        try:
                            conn.sendall(error_reply(None, "unavailable", str(error)))
                        except OSError:
                            return

    def start(self, directory):
        path = os.path.join(directory, "herdr.sock")
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(path)
        server.listen(8)

        def accept_loop():
            while True:
                try:
                    conn, _ = server.accept()
                except OSError:
                    return
                threading.Thread(target=self.serve, args=(conn,), daemon=True).start()

        threading.Thread(target=accept_loop, daemon=True).start()
        return server


class TeamError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


def error_reply(request_id, code, message):
    return (json.dumps({"id": request_id, "error": {"code": code, "message": message}}) + "\n").encode()
