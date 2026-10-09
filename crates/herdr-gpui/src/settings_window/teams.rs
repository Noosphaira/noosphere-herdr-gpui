//! The Teams section: edit the teams `herdr-launch` starts, the OpenCode
//! agents that fill their roles, and the folders sandboxed agents may reach.
//!
//! The files are owned by the `herdr-teams` script (`contrib/local-agents`),
//! which loads them as JSON and applies one validated change at a time, with
//! the same rules `herdr-launch` enforces. This section only edits drafts and
//! sends changes; it never writes those files itself.
//!
//! It edits the device selected in the main window, as Launch team launches
//! there: this computer, or a saved SSH host through a host script.

mod drafts;
mod model;
mod render;
mod ui;

pub(super) use drafts::{AccessScope, Drafts};
pub(super) use model::{Agent, Change, State, Team};

use crate::{multiline_input::MultilineInput, search_input::SearchInput, teleport::Host};
use gpui::{AppContext, Context, Entity};
use herdr_client::shell_quote;
use std::sync::atomic::AtomicBool;

/// Printed instead of the script's output when it is not installed, in the
/// shape of its own failures.
const NOT_INSTALLED: &str = r#"if ! command -v herdr-teams >/dev/null 2>&1; then
  printf '%s\n' '{"ok":false,"errors":["herdr-teams is not installed; run contrib/local-agents/install.sh"]}'
  exit 0
fi
"#;

#[derive(Debug, thiserror::Error)]
pub(super) enum Error {
    #[error("Could not run herdr-teams: {0}")]
    Script(#[source] herdr_client::Error),
    #[error("herdr-teams gave unexpected output")]
    Decode(#[source] serde_json::Error),
    /// herdr-teams refused the change; nothing was written.
    #[error("{}", .0.join("\n"))]
    Refused(Vec<String>),
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub(super) enum Tab {
    #[default]
    Teams,
    Agents,
    Access,
}

/// The device whose teams are being edited.
#[derive(Debug, Clone)]
pub(super) struct Device {
    /// The endpoint's id, to notice when the main window selects another.
    pub(super) id: String,
    pub(super) label: String,
    pub(super) host: Host,
}

/// What is selected in a list: a saved item by name, or a new one.
#[derive(Debug, Clone, PartialEq, Eq)]
pub(super) enum Selected {
    Saved(String),
    New,
}

pub(super) struct Inputs {
    pub(super) team_name: Entity<SearchInput>,
    pub(super) agent_name: Entity<SearchInput>,
    pub(super) agent_description: Entity<SearchInput>,
    pub(super) agent_temperature: Entity<SearchInput>,
    pub(super) agent_steps: Entity<SearchInput>,
    pub(super) agent_prompt: Entity<MultilineInput>,
    pub(super) access_path: Entity<SearchInput>,
}

pub(super) struct TeamsEditor {
    pub(super) tab: Tab,
    /// Where `state` came from and where changes go.
    pub(super) device: Option<Device>,
    pub(super) state: Option<State>,
    pub(super) loading: bool,
    pub(super) busy: bool,
    /// The last load or save failure, shown until the next success.
    pub(super) error: Option<String>,
    pub(super) notice: Option<String>,
    pub(super) team: Option<Selected>,
    pub(super) agent: Option<Selected>,
    pub(super) drafts: Drafts,
    pub(super) confirm_delete: bool,
    pub(super) inputs: Inputs,
}

impl TeamsEditor {
    pub(super) fn new<V: 'static>(cx: &mut Context<V>) -> Self {
        let field = |placeholder: &str, cx: &mut Context<V>| {
            let input = cx.new(SearchInput::new);
            input.update(cx, |input, cx| input.set_placeholder(placeholder, cx));
            input
        };
        let prompt = cx.new(MultilineInput::new);
        prompt.update(cx, |input, cx| {
            input.set_placeholder("What this agent does, and how it hands off to its team", cx);
            input.set_rows(8, 24, cx);
        });
        Self {
            tab: Tab::default(),
            device: None,
            state: None,
            loading: false,
            busy: false,
            error: None,
            notice: None,
            team: None,
            agent: None,
            drafts: Drafts::default(),
            confirm_delete: false,
            inputs: Inputs {
                team_name: field("team-name", cx),
                agent_name: field("agent-name", cx),
                agent_description: field("One line: what this agent is for", cx),
                agent_temperature: field("Model default", cx),
                agent_steps: field("No limit", cx),
                agent_prompt: prompt,
                access_path: field("A folder on this computer, e.g. ~/design-assets", cx),
            },
        }
    }
}

/// The script that loads (no change) or applies `change`.
pub(super) fn script(change: Option<&Change>) -> String {
    match change {
        None => format!("{NOT_INSTALLED}herdr-teams load\n"),
        Some(change) => {
            let json = serde_json::to_string(change).unwrap_or_default();
            format!(
                "{NOT_INSTALLED}printf '%s' {} | herdr-teams apply\n",
                shell_quote(&json)
            )
        }
    }
}

/// Blocking: run on a background executor.
pub(super) fn run(host: &Host, change: Option<&Change>) -> Result<State, Error> {
    let output = host
        .capture(&script(change), &AtomicBool::new(false))
        .map_err(Error::Script)?;
    model::decode(&output)
}

#[cfg(test)]
mod tests;
