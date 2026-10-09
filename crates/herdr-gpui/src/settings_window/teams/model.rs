//! The JSON `herdr-teams` speaks. Unknown keys round-trip through `extra`.

use super::Error;
use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub(in crate::settings_window) struct Team {
    pub(in crate::settings_window) name: String,
    /// Work order, which is also pane order.
    pub(in crate::settings_window) roles: Vec<String>,
    pub(in crate::settings_window) review_rounds: u32,
    #[serde(default)]
    pub(in crate::settings_window) extra: Map<String, Value>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub(in crate::settings_window) struct Agent {
    pub(in crate::settings_window) name: String,
    #[serde(default)]
    pub(in crate::settings_window) description: String,
    #[serde(default)]
    pub(in crate::settings_window) model: String,
    #[serde(default = "default_true")]
    pub(in crate::settings_window) thinking: bool,
    #[serde(default)]
    pub(in crate::settings_window) temperature: Option<f64>,
    #[serde(default)]
    pub(in crate::settings_window) steps: Option<u32>,
    #[serde(default)]
    pub(in crate::settings_window) prompt: String,
    #[serde(default)]
    pub(in crate::settings_window) extra: Map<String, Value>,
    #[serde(default)]
    pub(in crate::settings_window) extra_template_kwargs: Map<String, Value>,
}

fn default_true() -> bool {
    true
}

#[derive(Debug, Clone, PartialEq, Default, Serialize, Deserialize)]
pub(in crate::settings_window) struct Grants {
    #[serde(default)]
    pub(in crate::settings_window) read_only: Vec<String>,
    #[serde(default)]
    pub(in crate::settings_window) read_write: Vec<String>,
    /// Only for all repos: extra environment variables and PATH entries.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub(in crate::settings_window) env: Option<Vec<String>>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub(in crate::settings_window) path: Option<Vec<String>>,
}

#[derive(Debug, Clone, PartialEq, Default, Serialize, Deserialize)]
pub(in crate::settings_window) struct RepoGrants {
    pub(in crate::settings_window) repo: String,
    #[serde(default)]
    pub(in crate::settings_window) read_only: Vec<String>,
    #[serde(default)]
    pub(in crate::settings_window) read_write: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Default, Serialize, Deserialize)]
pub(in crate::settings_window) struct Access {
    pub(in crate::settings_window) global: Grants,
    #[serde(default)]
    pub(in crate::settings_window) repos: Vec<RepoGrants>,
}

#[derive(Debug, Clone, PartialEq, Default, Serialize, Deserialize)]
pub(in crate::settings_window) struct Paths {
    #[serde(default)]
    pub(in crate::settings_window) teams: String,
    #[serde(default)]
    pub(in crate::settings_window) agents: String,
    #[serde(default)]
    pub(in crate::settings_window) access: String,
}

#[derive(Debug, Clone, PartialEq, Default, Serialize, Deserialize)]
pub(in crate::settings_window) struct State {
    pub(in crate::settings_window) teams: Vec<Team>,
    pub(in crate::settings_window) agents: Vec<Agent>,
    pub(in crate::settings_window) access: Access,
    #[serde(default)]
    pub(in crate::settings_window) models: Vec<String>,
    #[serde(default)]
    pub(in crate::settings_window) paths: Paths,
}

/// One change for `herdr-teams apply`.
#[derive(Debug, Clone, PartialEq, Serialize)]
#[serde(tag = "op", rename_all = "snake_case")]
pub(in crate::settings_window) enum Change {
    SaveTeam {
        team: Team,
        previous: Option<String>,
    },
    DeleteTeam {
        name: String,
    },
    SaveAgent {
        agent: Agent,
        previous: Option<String>,
    },
    DeleteAgent {
        name: String,
    },
    SaveAccess {
        access: Access,
    },
}

#[derive(Deserialize)]
struct Reply {
    ok: bool,
    #[serde(default)]
    state: Option<State>,
    #[serde(default)]
    errors: Vec<String>,
}

/// The reply is the last non-empty line of the script's output.
pub(super) fn decode(output: &[u8]) -> Result<State, Error> {
    let text = String::from_utf8_lossy(output);
    let last = text
        .lines()
        .rev()
        .find(|line| !line.trim().is_empty())
        .unwrap_or("");
    let reply: Reply = serde_json::from_str(last).map_err(Error::Decode)?;
    match (reply.ok, reply.state) {
        (true, Some(state)) => Ok(state),
        _ if reply.errors.is_empty() => Err(Error::Refused(vec!["herdr-teams failed".into()])),
        _ => Err(Error::Refused(reply.errors)),
    }
}
