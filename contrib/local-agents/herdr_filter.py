"""The only route from a sandboxed agent to herdr.

herdr's control socket can run commands in any pane, so it is never bound
into a sandbox. Each sandbox gets this filter's socket instead, which allows:

- the agent-state reports OpenCode's herdr integration sends, for the
  agent's own pane only;
- with a team, the `team.*` methods the `team` command uses: read the task
  and the order of roles, see teammates' status, message a teammate, and
  mark the team finished.

A team message becomes herdr's `agent.prompt` to a teammate's pane, which
herdr only delivers to a recognised agent, never to a bare shell. The sender
is set here, not by the agent, and review rounds are capped here, so an
agent cannot impersonate a teammate or keep a feedback loop going forever.
"""

import json
import os
import socket
import threading

import herdr_team

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

    def next_role(self):
        flow = self.team["flow"]
        index = flow.index(self.role)
        return flow[index + 1] if index + 1 < len(flow) else None

    def team_info(self, _params):
        current = herdr_team.read_state(self.team_dir)
        return {
            "role": self.role,
            "flow": self.team["flow"],
            "next": self.next_role(),
            "task": self.team["task"],
            "branch": self.team["branch"],
            "active": current["active"],
            "done": current["done"],
            "review_rounds_left": max(0, self.team["review_rounds"] - current["rounds"]),
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

    def team_send(self, params):
        to = params.get("to")
        text = herdr_team.clean(params.get("text", ""), herdr_team.MAX_MESSAGE).strip()
        flow = self.team["flow"]
        if to not in self.team["members"]:
            raise TeamError("unknown_role", f"no teammate {to!r}; roles are {', '.join(flow)}")
        if to == self.role:
            raise TeamError("invalid", "you cannot message yourself")
        if not text:
            raise TeamError("invalid", "the message is empty")
        feedback = herdr_team.is_feedback(flow, self.role, to)
        with herdr_team.state(self.team_dir) as current:
            if current["done"]:
                raise TeamError("finished", "the team has already finished")
            if feedback and current["rounds"] >= self.team["review_rounds"]:
                raise TeamError(
                    "round_limit",
                    "no review rounds left; list what is still open with `team done`",
                )
            if feedback:
                current["rounds"] += 1
        message = (
            f"[team message from {self.role}] {text}\n"
            f"(Run `team info` for the original task. Reply with `team send {self.role} ...`.)"
        )
        result, error = self.herdr("agent.prompt", {"target": self.team["members"][to], "text": message})
        if error:
            if feedback:
                with herdr_team.state(self.team_dir) as current:
                    current["rounds"] -= 1
            raise TeamError(error.get("code", "herdr_error"), error.get("message", "herdr refused"))
        with herdr_team.state(self.team_dir) as current:
            current["active"] = to
            current["log"].append({"from": self.role, "to": to, "feedback": feedback, "text": text[:200]})
        return {"delivered_to": to}

    def team_done(self, params):
        summary = herdr_team.clean(params.get("summary", ""), herdr_team.MAX_NOTE).strip()
        with herdr_team.state(self.team_dir) as current:
            current["done"] = True
            current["summary"] = summary
            current["log"].append({"from": self.role, "done": True, "text": summary[:200]})
        herdr_team.notify(f"Team finished: {self.team['branch']}", summary or f"{self.role} marked it done")
        return {"done": True}

    TEAM_METHODS = {
        "team.info": team_info,
        "team.status": team_status,
        "team.send": team_send,
        "team.done": team_done,
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
