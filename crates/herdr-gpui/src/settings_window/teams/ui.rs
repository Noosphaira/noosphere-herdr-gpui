//! Loading, selecting, and saving in the Teams section.

use super::{
    AccessScope, Change, Selected, State, Tab,
    drafts::{FieldError, Mode},
};
use crate::settings_window::SettingsWindow;
use gpui::{prelude::*, *};

/// What to select once a change has been saved.
pub(super) enum After {
    Team(Option<String>),
    Agent(Option<String>),
    Access,
}

impl SettingsWindow {
    pub(in crate::settings_window) fn load_teams(&mut self, cx: &mut Context<Self>) {
        // Tests supply their state directly instead of reading this
        // machine's files through herdr-teams.
        if cfg!(test) || self.teams.loading || self.teams.busy {
            return;
        }
        self.teams.loading = true;
        let task = cx
            .background_executor()
            .spawn(async move { super::run(None) });
        cx.spawn(async move |this, cx| {
            let result = task.await;
            let _ = this.update(cx, |this, cx| {
                this.teams.loading = false;
                match result {
                    Ok(state) => {
                        this.teams.error = None;
                        this.adopt_teams_state(state, None, cx);
                    }
                    Err(error) => this.teams.error = Some(error.to_string()),
                }
                cx.notify();
            });
        })
        .detach();
        cx.notify();
    }

    pub(super) fn apply_teams_change(
        &mut self,
        change: Change,
        after: After,
        cx: &mut Context<Self>,
    ) {
        if self.teams.busy {
            return;
        }
        self.teams.busy = true;
        self.teams.notice = None;
        let task = cx
            .background_executor()
            .spawn(async move { super::run(Some(&change)) });
        cx.spawn(async move |this, cx| {
            let result = task.await;
            let _ = this.update(cx, |this, cx| {
                this.teams.busy = false;
                match result {
                    Ok(state) => {
                        this.teams.error = None;
                        this.teams.notice = Some("Saved. Changes apply to the next launch.".into());
                        this.adopt_teams_state(state, Some(after), cx);
                    }
                    Err(error) => this.teams.error = Some(error.to_string()),
                }
                cx.notify();
            });
        })
        .detach();
        cx.notify();
    }

    /// Take a freshly loaded state; reload the drafts that a save touched,
    /// and drop selections whose item no longer exists.
    pub(super) fn adopt_teams_state(
        &mut self,
        state: State,
        after: Option<After>,
        cx: &mut Context<Self>,
    ) {
        let first_load = self.teams.state.is_none();
        self.teams.state = Some(state);
        self.teams.confirm_delete = false;
        match after {
            Some(After::Team(name)) => self.select_team(name.map(Selected::Saved), cx),
            Some(After::Agent(name)) => self.select_agent(name.map(Selected::Saved), cx),
            Some(After::Access) => self.reset_access(),
            None => {
                let keep_team = self.teams.team.clone().filter(|s| self.exists_team(s));
                let keep_agent = self.teams.agent.clone().filter(|s| self.exists_agent(s));
                if keep_team != self.teams.team {
                    self.select_team(keep_team, cx);
                }
                if keep_agent != self.teams.agent {
                    self.select_agent(keep_agent, cx);
                }
                if first_load {
                    self.reset_access();
                }
            }
        }
    }

    fn exists_team(&self, selected: &Selected) -> bool {
        match selected {
            Selected::New => true,
            Selected::Saved(name) => self.saved_team(name).is_some(),
        }
    }

    fn exists_agent(&self, selected: &Selected) -> bool {
        match selected {
            Selected::New => true,
            Selected::Saved(name) => self.saved_agent(name).is_some(),
        }
    }

    fn saved_team(&self, name: &str) -> Option<&super::Team> {
        self.teams
            .state
            .as_ref()?
            .teams
            .iter()
            .find(|t| t.name == name)
    }

    fn saved_agent(&self, name: &str) -> Option<&super::Agent> {
        self.teams
            .state
            .as_ref()?
            .agents
            .iter()
            .find(|a| a.name == name)
    }

    pub(super) fn select_team(&mut self, selected: Option<Selected>, cx: &mut Context<Self>) {
        let team = match &selected {
            Some(Selected::Saved(name)) => self.saved_team(name).cloned(),
            _ => None,
        };
        self.teams.drafts.load_team(team.as_ref());
        let name = team.map(|t| t.name).unwrap_or_default();
        self.teams
            .inputs
            .team_name
            .update(cx, |input, cx| input.set_text(&name, cx));
        self.teams.team = selected;
        self.teams.confirm_delete = false;
        cx.notify();
    }

    pub(super) fn select_agent(&mut self, selected: Option<Selected>, cx: &mut Context<Self>) {
        let agent = match &selected {
            Some(Selected::Saved(name)) => self.saved_agent(name).cloned(),
            _ => None,
        };
        let default_model = self
            .teams
            .state
            .as_ref()
            .and_then(|s| s.models.first().cloned())
            .unwrap_or_default();
        self.teams.drafts.load_agent(agent.as_ref(), &default_model);
        let inputs = &self.teams.inputs;
        let (name, description, temperature, steps, prompt) = match &agent {
            Some(a) => (
                a.name.clone(),
                a.description.clone(),
                a.temperature.map(|t| t.to_string()).unwrap_or_default(),
                a.steps.map(|s| s.to_string()).unwrap_or_default(),
                a.prompt.clone(),
            ),
            None => Default::default(),
        };
        inputs.agent_name.update(cx, |i, cx| i.set_text(&name, cx));
        inputs
            .agent_description
            .update(cx, |i, cx| i.set_text(&description, cx));
        inputs
            .agent_temperature
            .update(cx, |i, cx| i.set_text(&temperature, cx));
        inputs
            .agent_steps
            .update(cx, |i, cx| i.set_text(&steps, cx));
        inputs
            .agent_prompt
            .update(cx, |i, cx| i.set_text(&prompt, cx));
        self.teams.agent = selected;
        self.teams.confirm_delete = false;
        cx.notify();
    }

    pub(super) fn reset_access(&mut self) {
        self.teams.drafts.access = self
            .teams
            .state
            .as_ref()
            .map(|s| s.access.clone())
            .unwrap_or_default();
        self.teams.drafts.scope = AccessScope::Global;
    }

    fn team_draft(&self, cx: &App) -> Result<super::Team, FieldError> {
        let name = self.teams.inputs.team_name.read(cx).text().to_owned();
        self.teams.drafts.team(&name)
    }

    fn agent_draft(&self, cx: &App) -> Result<super::Agent, FieldError> {
        let inputs = &self.teams.inputs;
        self.teams.drafts.agent(
            inputs.agent_name.read(cx).text(),
            inputs.agent_description.read(cx).text(),
            inputs.agent_temperature.read(cx).text(),
            inputs.agent_steps.read(cx).text(),
            inputs.agent_prompt.read(cx).text(),
        )
    }

    pub(super) fn team_dirty(&self, cx: &App) -> bool {
        match &self.teams.team {
            Some(Selected::Saved(name)) => {
                self.team_draft(cx).ok().as_ref() != self.saved_team(name)
            }
            Some(Selected::New) => true,
            None => false,
        }
    }

    pub(super) fn agent_dirty(&self, cx: &App) -> bool {
        match &self.teams.agent {
            Some(Selected::Saved(name)) => {
                self.agent_draft(cx).ok().as_ref() != self.saved_agent(name)
            }
            Some(Selected::New) => true,
            None => false,
        }
    }

    pub(super) fn access_dirty(&self) -> bool {
        self.teams.state.as_ref().map(|s| &s.access) != Some(&self.teams.drafts.access)
    }

    fn previous(selected: &Option<Selected>) -> Option<String> {
        match selected {
            Some(Selected::Saved(name)) => Some(name.clone()),
            _ => None,
        }
    }

    pub(super) fn save_team(&mut self, cx: &mut Context<Self>) {
        match self.team_draft(cx) {
            Ok(team) => {
                let name = team.name.clone();
                let previous = Self::previous(&self.teams.team);
                self.apply_teams_change(
                    Change::SaveTeam { team, previous },
                    After::Team(Some(name)),
                    cx,
                );
            }
            Err(error) => self.teams.error = Some(error.message().into()),
        }
        cx.notify();
    }

    pub(super) fn save_agent(&mut self, cx: &mut Context<Self>) {
        match self.agent_draft(cx) {
            Ok(agent) => {
                let name = agent.name.clone();
                let previous = Self::previous(&self.teams.agent);
                self.apply_teams_change(
                    Change::SaveAgent { agent, previous },
                    After::Agent(Some(name)),
                    cx,
                );
            }
            Err(error) => self.teams.error = Some(error.message().into()),
        }
        cx.notify();
    }

    pub(super) fn delete_selected(&mut self, cx: &mut Context<Self>) {
        if !self.teams.confirm_delete {
            self.teams.confirm_delete = true;
            cx.notify();
            return;
        }
        let (change, after) = match self.teams.tab {
            Tab::Teams => match Self::previous(&self.teams.team) {
                Some(name) => (Change::DeleteTeam { name }, After::Team(None)),
                None => return self.select_team(None, cx),
            },
            Tab::Agents => match Self::previous(&self.teams.agent) {
                Some(name) => (Change::DeleteAgent { name }, After::Agent(None)),
                None => return self.select_agent(None, cx),
            },
            Tab::Access => return,
        };
        self.apply_teams_change(change, after, cx);
    }

    /// Pick a folder with the desktop's file chooser: a repository to add
    /// grants for, or a folder to grant.
    pub(super) fn browse_teams_folder(&mut self, repo: bool, cx: &mut Context<Self>) {
        let picked = cx.prompt_for_paths(PathPromptOptions {
            files: false,
            directories: true,
            multiple: false,
            prompt: Some(
                if repo {
                    "Choose repository"
                } else {
                    "Choose folder"
                }
                .into(),
            ),
        });
        cx.spawn(async move |this, cx| {
            let Ok(Ok(Some(paths))) = picked.await else {
                return;
            };
            let Some(path) = paths.into_iter().next() else {
                return;
            };
            let text = path.to_string_lossy().into_owned();
            let _ = this.update(cx, |this, cx| {
                if repo {
                    this.teams.drafts.add_repo(&text);
                } else {
                    this.teams
                        .inputs
                        .access_path
                        .update(cx, |i, cx| i.set_text(&text, cx));
                }
                cx.notify();
            });
        })
        .detach();
    }

    pub(super) fn add_grant(&mut self, mode: Mode, cx: &mut Context<Self>) {
        let path = self.teams.inputs.access_path.read(cx).text().to_owned();
        if self.teams.drafts.grant(&path, mode) {
            self.teams
                .inputs
                .access_path
                .update(cx, |i, cx| i.clear(cx));
        }
        cx.notify();
    }
}
