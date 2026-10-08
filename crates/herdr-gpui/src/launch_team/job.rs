//! The blocking work behind a team launch. Every function here runs a host
//! script and must only be called from a background worker.

use super::error::{Error, Result};
use crate::teleport::Host;
use herdr_client::shell_quote;
use serde::Deserialize;
use std::{collections::BTreeMap, sync::atomic::AtomicBool};

/// Printed in place of the script's own output when it is not installed, in
/// the same shape as its failures.
const NOT_INSTALLED: &str = r#"if ! command -v herdr-launch >/dev/null 2>&1; then
  printf '%s\n' '{"ok":false,"step":"setup","error":"herdr-launch is not installed on this host (see contrib/local-agents)","output":""}'
  exit 0
fi
"#;

/// What the dialog asks `herdr-launch` to do.
#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct Request {
    pub(crate) team: String,
    /// Any path in the repository; the main checkout's `.git` in practice.
    pub(crate) repo: String,
    pub(crate) branch: String,
    pub(crate) task: String,
    /// The ref the branch starts from, when not the main checkout's `HEAD`.
    pub(crate) base: Option<String>,
}

/// A launched team: its workspace, and the pane each role runs in.
#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
pub(crate) struct Launched {
    pub(crate) workspace_id: String,
    pub(crate) worktree: String,
    pub(crate) branch: String,
    pub(crate) panes: BTreeMap<String, String>,
}

#[derive(Debug, Deserialize)]
struct Failure {
    step: String,
    error: String,
    #[serde(default)]
    output: String,
}

impl From<Failure> for Error {
    fn from(failure: Failure) -> Self {
        Self::Failed {
            step: failure.step,
            message: failure.error,
            output: failure.output,
        }
    }
}

#[derive(Debug, Deserialize)]
#[serde(untagged)]
enum Outcome<T> {
    Done(T),
    Failed(Failure),
}

#[derive(Debug, Deserialize)]
struct Teams {
    teams: Vec<String>,
}

/// The team names configured on `host`.
pub(crate) fn teams(host: &Host, cancelled: &AtomicBool) -> Result<Vec<String>> {
    let output = host.capture(
        &format!("{NOT_INSTALLED}herdr-launch --list-teams\n"),
        cancelled,
    )?;
    decode::<Teams>(&output).map(|teams| teams.teams)
}

/// Launch `request` on `host`. `herdr-launch` exits non-zero on failure but
/// still prints its JSON report, so its exit status is not checked here.
pub(crate) fn launch(host: &Host, request: &Request, cancelled: &AtomicBool) -> Result<Launched> {
    let output = host.capture(&script(host.session(), request), cancelled)?;
    decode(&output)
}

/// The script line running `herdr-launch` for `request`, every value quoted.
pub(crate) fn script(session: Option<&str>, request: &Request) -> String {
    let mut args = vec!["herdr-launch", "--json"];
    if let Some(session) = session {
        args.extend(["--session", session]);
    }
    args.extend([
        "--team",
        &request.team,
        "--repo",
        &request.repo,
        "--branch",
        &request.branch,
        "--task",
        &request.task,
    ]);
    if let Some(base) = &request.base {
        args.extend(["--base", base]);
    }
    let line = args
        .iter()
        .map(|arg| shell_quote(arg))
        .collect::<Vec<_>>()
        .join(" ");
    format!("{NOT_INSTALLED}{line} || true\n")
}

/// The last line of `output` is the report; progress goes to stderr.
pub(crate) fn decode<T: serde::de::DeserializeOwned>(output: &[u8]) -> Result<T> {
    let text = String::from_utf8_lossy(output);
    let last = text
        .lines()
        .rev()
        .find(|line| !line.trim().is_empty())
        .unwrap_or("");
    match serde_json::from_str::<Outcome<T>>(last)? {
        Outcome::Done(value) => Ok(value),
        Outcome::Failed(failure) => Err(failure.into()),
    }
}
