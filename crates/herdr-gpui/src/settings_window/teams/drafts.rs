//! The parts of a draft that are not text fields, and turning a whole draft
//! back into the item `herdr-teams` saves.

use super::model::{Access, Agent, RepoGrants, Team};
use serde_json::{Map, Value};

/// Which folder grants the access editor shows.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub(in crate::settings_window) enum AccessScope {
    #[default]
    Global,
    Repo(usize),
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(in crate::settings_window) enum Mode {
    ReadOnly,
    ReadWrite,
}

impl Mode {
    pub(in crate::settings_window) fn label(self) -> &'static str {
        match self {
            Self::ReadOnly => "Read-only",
            Self::ReadWrite => "Read-write",
        }
    }
}

/// Why a typed field cannot be saved.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(in crate::settings_window) enum FieldError {
    Name,
    Temperature,
    Steps,
}

impl FieldError {
    pub(in crate::settings_window) fn message(self) -> &'static str {
        match self {
            Self::Name => "Names use lowercase letters, digits and dashes, starting with a letter",
            Self::Temperature => {
                "Temperature is a number from 0 to 2, or empty for the model's default"
            }
            Self::Steps => "Step limit is a whole number from 1 to 1000, or empty for no limit",
        }
    }
}

#[derive(Debug, Clone, PartialEq, Default)]
pub(in crate::settings_window) struct Drafts {
    pub(in crate::settings_window) team_roles: Vec<String>,
    pub(in crate::settings_window) team_rounds: u32,
    team_extra: Map<String, Value>,
    pub(in crate::settings_window) agent_model: String,
    pub(in crate::settings_window) agent_thinking: bool,
    agent_extra: Map<String, Value>,
    agent_extra_kwargs: Map<String, Value>,
    pub(in crate::settings_window) access: Access,
    pub(in crate::settings_window) scope: AccessScope,
}

pub(in crate::settings_window) fn valid_name(name: &str) -> bool {
    let mut chars = name.chars();
    chars.next().is_some_and(|c| c.is_ascii_lowercase())
        && name.len() <= 40
        && chars.all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == '-')
}

/// Empty means unset; anything else must parse and be in range.
fn optional<T: std::str::FromStr + PartialOrd>(
    text: &str,
    low: T,
    high: T,
) -> Result<Option<T>, ()> {
    let text = text.trim();
    if text.is_empty() {
        return Ok(None);
    }
    match text.parse::<T>() {
        Ok(value) if value >= low && value <= high => Ok(Some(value)),
        _ => Err(()),
    }
}

impl Drafts {
    pub(in crate::settings_window) fn load_team(&mut self, team: Option<&Team>) {
        self.team_roles = team.map(|t| t.roles.clone()).unwrap_or_default();
        self.team_rounds = team.map_or(2, |t| t.review_rounds);
        self.team_extra = team.map(|t| t.extra.clone()).unwrap_or_default();
    }

    pub(in crate::settings_window) fn team(&self, name: &str) -> Result<Team, FieldError> {
        let name = name.trim();
        if !valid_name(name) {
            return Err(FieldError::Name);
        }
        Ok(Team {
            name: name.to_owned(),
            roles: self.team_roles.clone(),
            review_rounds: self.team_rounds,
            extra: self.team_extra.clone(),
        })
    }

    /// Move the role at `index` by `delta` places; false if it cannot move.
    pub(in crate::settings_window) fn move_role(&mut self, index: usize, delta: isize) -> bool {
        let Some(target) = index
            .checked_add_signed(delta)
            .filter(|t| *t < self.team_roles.len())
        else {
            return false;
        };
        self.team_roles.swap(index, target);
        true
    }

    pub(in crate::settings_window) fn load_agent(
        &mut self,
        agent: Option<&Agent>,
        default_model: &str,
    ) {
        self.agent_model = agent.map_or_else(|| default_model.to_owned(), |a| a.model.clone());
        self.agent_thinking = agent.is_none_or(|a| a.thinking);
        self.agent_extra = agent.map(|a| a.extra.clone()).unwrap_or_default();
        self.agent_extra_kwargs = agent
            .map(|a| a.extra_template_kwargs.clone())
            .unwrap_or_default();
    }

    pub(in crate::settings_window) fn agent(
        &self,
        name: &str,
        description: &str,
        temperature: &str,
        steps: &str,
        prompt: &str,
    ) -> Result<Agent, FieldError> {
        let name = name.trim();
        if !valid_name(name) {
            return Err(FieldError::Name);
        }
        let temperature =
            optional(temperature, 0.0_f64, 2.0).map_err(|()| FieldError::Temperature)?;
        let steps = optional(steps, 1_u32, 1000).map_err(|()| FieldError::Steps)?;
        Ok(Agent {
            name: name.to_owned(),
            description: description.trim().to_owned(),
            model: self.agent_model.clone(),
            thinking: self.agent_thinking,
            temperature,
            steps,
            prompt: prompt.trim_end().to_owned(),
            extra: self.agent_extra.clone(),
            extra_template_kwargs: self.agent_extra_kwargs.clone(),
        })
    }

    /// The grants the scope shows, as (path, mode) rows.
    pub(in crate::settings_window) fn grants(&self) -> Vec<(String, Mode)> {
        let (read_only, read_write) = match self.scope {
            AccessScope::Global => (
                &self.access.global.read_only,
                &self.access.global.read_write,
            ),
            AccessScope::Repo(index) => match self.access.repos.get(index) {
                Some(repo) => (&repo.read_only, &repo.read_write),
                None => return Vec::new(),
            },
        };
        read_only
            .iter()
            .map(|p| (p.clone(), Mode::ReadOnly))
            .chain(read_write.iter().map(|p| (p.clone(), Mode::ReadWrite)))
            .collect()
    }

    fn lists(&mut self) -> Option<(&mut Vec<String>, &mut Vec<String>)> {
        match self.scope {
            AccessScope::Global => Some((
                &mut self.access.global.read_only,
                &mut self.access.global.read_write,
            )),
            AccessScope::Repo(index) => self
                .access
                .repos
                .get_mut(index)
                .map(|repo| (&mut repo.read_only, &mut repo.read_write)),
        }
    }

    /// Grant `path` in the current scope with `mode`, replacing any grant of
    /// the same path. Returns whether anything changed.
    pub(in crate::settings_window) fn grant(&mut self, path: &str, mode: Mode) -> bool {
        let path = path.trim();
        if path.is_empty() {
            return false;
        }
        let Some((read_only, read_write)) = self.lists() else {
            return false;
        };
        read_only.retain(|p| p != path);
        read_write.retain(|p| p != path);
        match mode {
            Mode::ReadOnly => read_only.push(path.to_owned()),
            Mode::ReadWrite => read_write.push(path.to_owned()),
        }
        true
    }

    pub(in crate::settings_window) fn revoke(&mut self, path: &str) {
        if let Some((read_only, read_write)) = self.lists() {
            read_only.retain(|p| p != path);
            read_write.retain(|p| p != path);
        }
    }

    /// Add a repository entry (or select its existing one).
    pub(in crate::settings_window) fn add_repo(&mut self, repo: &str) {
        let repo = repo.trim();
        if repo.is_empty() {
            return;
        }
        let index = match self.access.repos.iter().position(|r| r.repo == repo) {
            Some(index) => index,
            None => {
                self.access.repos.push(RepoGrants {
                    repo: repo.to_owned(),
                    ..RepoGrants::default()
                });
                self.access.repos.len() - 1
            }
        };
        self.scope = AccessScope::Repo(index);
    }

    pub(in crate::settings_window) fn remove_repo(&mut self) {
        if let AccessScope::Repo(index) = self.scope
            && index < self.access.repos.len()
        {
            self.access.repos.remove(index);
            self.scope = AccessScope::Global;
        }
    }
}
