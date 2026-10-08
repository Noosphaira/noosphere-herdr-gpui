//! Drawing the Teams section: one tab each for teams, agents, and folder access.

use super::{AccessScope, Change, Selected, Tab, drafts::Mode, ui::After};
use crate::{config::corners, settings_window::SettingsWindow};
use gpui::{prelude::*, *};

impl SettingsWindow {
    pub(in crate::settings_window) fn render_teams(&self, cx: &mut Context<Self>) -> Div {
        let tabs = div().flex().flex_wrap().gap(px(8.)).children(
            [
                (Tab::Teams, "Teams"),
                (Tab::Agents, "Agents"),
                (Tab::Access, "Folder access"),
            ]
            .map(|(tab, label)| {
                self.control_choice(
                    format!("teams-tab-{label}"),
                    label,
                    self.teams.tab == tab,
                    true,
                )
                .on_click(cx.listener(move |this, _, _, cx| {
                    this.teams.tab = tab;
                    this.teams.confirm_delete = false;
                    cx.notify();
                }))
            }),
        );
        let mut page = div().flex().flex_col().gap(px(24.)).child(tabs);
        let status = self.teams_status();
        if self.teams.state.is_none() {
            let note = if self.teams.loading {
                "Loading..."
            } else {
                "Not loaded."
            };
            return page.children(status).child(self.control_note(note));
        }
        page = page.children(status);
        match self.teams.tab {
            Tab::Teams => page
                .child(self.render_team_list(cx))
                .children(self.render_team_editor(cx)),
            Tab::Agents => page
                .child(self.render_agent_list(cx))
                .children(self.render_agent_editor(cx)),
            Tab::Access => page.child(self.render_access(cx)),
        }
        .child(self.control_note(format!("Files: {}", self.teams_files())))
    }

    fn teams_files(&self) -> String {
        let Some(state) = &self.teams.state else {
            return String::new();
        };
        match self.teams.tab {
            Tab::Teams => state.paths.teams.clone(),
            Tab::Agents => state.paths.agents.clone(),
            Tab::Access => state.paths.access.clone(),
        }
    }

    fn teams_status(&self) -> Option<Div> {
        let theme = &self.theme;
        if let Some(error) = &self.teams.error {
            return Some(
                div()
                    .debug_selector(|| "teams-error".into())
                    .text_color(crate::menu::danger(theme))
                    .children(error.lines().map(|line| div().child(line.to_owned()))),
            );
        }
        self.teams.notice.as_ref().map(|notice| {
            div()
                .debug_selector(|| "teams-notice".into())
                .text_color(rgb(theme.muted))
                .child(notice.clone())
        })
    }

    fn teams_button(
        &self,
        id: &'static str,
        label: &'static str,
        primary: bool,
        enabled: bool,
    ) -> Stateful<Div> {
        let theme = &self.theme;
        let enabled = enabled && !self.teams.busy;
        div()
            .id(id)
            .debug_selector(move || id.into())
            .px(px(14.))
            .py(px(7.))
            .rounded(px(corners::CONTROL))
            .border_1()
            .when(primary && enabled, |b| {
                b.bg(crate::menu::accent(theme))
                    .border_color(crate::menu::accent(theme))
                    .text_color(rgb(theme.background))
            })
            .when(!primary || !enabled, |b| b.border_color(rgb(theme.active)))
            .when(enabled, |b| b.cursor_pointer().hover(|s| s.opacity(0.85)))
            .when(!enabled, |b| b.opacity(0.45))
            .child(label)
    }

    fn small_button(&self, id: impl Into<SharedString>, label: &'static str) -> Stateful<Div> {
        let theme = &self.theme;
        let id = id.into();
        div()
            .id(ElementId::Name(id.clone()))
            .debug_selector(move || id.to_string())
            .px(px(8.))
            .py(px(2.))
            .rounded(px(corners::CONTROL))
            .border_1()
            .border_color(rgb(theme.active))
            .cursor_pointer()
            .hover(|s| s.bg(rgb(theme.active)))
            .child(label)
    }

    fn field_row(&self, label: &'static str, field: impl IntoElement) -> Div {
        div()
            .flex()
            .items_center()
            .gap(px(12.))
            .child(
                div()
                    .flex_none()
                    .w(px(self.config.ui.size * 9.))
                    .text_color(rgb(self.theme.muted))
                    .child(label),
            )
            .child(div().flex_1().min_w_0().child(field))
    }

    /// Save, revert and delete, for the team or agent being edited.
    fn editor_actions(&self, dirty: bool, saved: bool, cx: &mut Context<Self>) -> Div {
        let delete = if self.teams.confirm_delete {
            "Confirm delete"
        } else {
            "Delete"
        };
        div()
            .flex()
            .flex_wrap()
            .items_center()
            .gap(px(8.))
            .child(
                self.teams_button("teams-save", "Save", true, dirty)
                    .when(dirty, |b| {
                        b.on_click(cx.listener(|this, _, _, cx| match this.teams.tab {
                            Tab::Teams => this.save_team(cx),
                            Tab::Agents => this.save_agent(cx),
                            Tab::Access => {}
                        }))
                    }),
            )
            .child(
                self.teams_button("teams-revert", "Revert", false, dirty && saved)
                    .when(dirty && saved, |b| {
                        b.on_click(cx.listener(|this, _, _, cx| {
                            this.teams.error = None;
                            match this.teams.tab {
                                Tab::Teams => this.select_team(this.teams.team.clone(), cx),
                                Tab::Agents => this.select_agent(this.teams.agent.clone(), cx),
                                Tab::Access => {}
                            }
                        }))
                    }),
            )
            .child(div().flex_1())
            .child(
                self.teams_button(
                    "teams-delete",
                    if saved { delete } else { "Discard" },
                    false,
                    true,
                )
                .when(self.teams.confirm_delete, |b| {
                    b.text_color(crate::menu::danger(&self.theme))
                })
                .on_click(cx.listener(|this, _, _, cx| {
                    if matches!(this.teams.tab, Tab::Teams)
                        && this.teams.team == Some(Selected::New)
                    {
                        return this.select_team(None, cx);
                    }
                    if matches!(this.teams.tab, Tab::Agents)
                        && this.teams.agent == Some(Selected::New)
                    {
                        return this.select_agent(None, cx);
                    }
                    this.delete_selected(cx);
                })),
            )
    }

    fn render_team_list(&self, cx: &mut Context<Self>) -> Div {
        let state = self.teams.state.as_ref();
        let names: Vec<String> = state
            .map(|s| s.teams.iter().map(|t| t.name.clone()).collect())
            .unwrap_or_default();
        self.control_card("Teams").child(
            div()
                .flex()
                .flex_wrap()
                .gap(px(8.))
                .children(names.into_iter().map(|name| {
                    let selected = self.teams.team == Some(Selected::Saved(name.clone()));
                    let pick = name.clone();
                    self.control_choice(format!("teams-team-{name}"), name, selected, true)
                        .on_click(cx.listener(move |this, _, _, cx| {
                            this.teams.error = None;
                            this.teams.notice = None;
                            this.select_team(Some(Selected::Saved(pick.clone())), cx);
                        }))
                }))
                .child(
                    self.control_choice(
                        "teams-team-new",
                        "+ New team",
                        self.teams.team == Some(Selected::New),
                        true,
                    )
                    .on_click(cx.listener(|this, _, _, cx| {
                        this.teams.notice = None;
                        this.select_team(Some(Selected::New), cx);
                    })),
                ),
        )
    }

    fn render_team_editor(&self, cx: &mut Context<Self>) -> Option<Div> {
        let selected = self.teams.team.clone()?;
        let drafts = &self.teams.drafts;
        let agents: Vec<String> = self
            .teams
            .state
            .as_ref()
            .map(|s| s.agents.iter().map(|a| a.name.clone()).collect())
            .unwrap_or_default();
        let mut roles = div().flex().flex_col().gap(px(6.));
        let count = drafts.team_roles.len();
        for (index, role) in drafts.team_roles.iter().enumerate() {
            roles = roles.child(
                div()
                    .flex()
                    .items_center()
                    .gap(px(8.))
                    .child(
                        div()
                            .w(px(24.))
                            .text_color(rgb(self.theme.muted))
                            .child(format!("{}.", index + 1)),
                    )
                    .child(div().flex_1().min_w_0().truncate().child(role.clone()))
                    .when(index > 0, |row| {
                        row.child(
                            self.small_button(format!("teams-role-up-{index}"), "↑")
                                .on_click(cx.listener(move |this, _, _, cx| {
                                    this.teams.drafts.move_role(index, -1);
                                    cx.notify();
                                })),
                        )
                    })
                    .when(index + 1 < count, |row| {
                        row.child(
                            self.small_button(format!("teams-role-down-{index}"), "↓")
                                .on_click(cx.listener(move |this, _, _, cx| {
                                    this.teams.drafts.move_role(index, 1);
                                    cx.notify();
                                })),
                        )
                    })
                    .child(
                        self.small_button(format!("teams-role-remove-{index}"), "×")
                            .on_click(cx.listener(move |this, _, _, cx| {
                                this.teams.drafts.team_roles.remove(index);
                                cx.notify();
                            })),
                    ),
            );
        }
        if count == 0 {
            roles = roles.child(self.control_note("No roles yet: add agents below."));
        }
        let unused: Vec<String> = agents
            .into_iter()
            .filter(|a| !drafts.team_roles.contains(a))
            .collect();
        let add = div()
            .flex()
            .flex_wrap()
            .gap(px(6.))
            .children(unused.into_iter().map(|agent| {
                let pick = agent.clone();
                self.small_button(format!("teams-role-add-{agent}"), "+")
                    .child(format!(" {agent}"))
                    .on_click(cx.listener(move |this, _, _, cx| {
                        this.teams.drafts.team_roles.push(pick.clone());
                        cx.notify();
                    }))
            }));
        let rounds = drafts.team_rounds;
        let stepper = div()
            .flex()
            .items_center()
            .gap(px(10.))
            .child(
                self.small_button("teams-rounds-less", "−")
                    .on_click(cx.listener(|this, _, _, cx| {
                        this.teams.drafts.team_rounds =
                            this.teams.drafts.team_rounds.saturating_sub(1);
                        cx.notify();
                    })),
            )
            .child(
                div()
                    .debug_selector(|| "teams-rounds".into())
                    .child(rounds.to_string()),
            )
            .child(
                self.small_button("teams-rounds-more", "+")
                    .on_click(cx.listener(|this, _, _, cx| {
                        this.teams.drafts.team_rounds = (this.teams.drafts.team_rounds + 1).min(10);
                        cx.notify();
                    })),
            );
        let saved = matches!(selected, Selected::Saved(_));
        Some(
            self.control_card(if saved { "Team" } else { "New team" })
                .child(self.field_row("Name", self.teams.inputs.team_name.clone()))
                .child(self.control_note("Roles, in work order: the first gets the task, each hands off to the next. Panes follow the same order."))
                .child(roles)
                .child(self.field_row("Add a role", add))
                .child(self.field_row("Review rounds", stepper))
                .child(self.control_note("How many times later roles may send work back to an earlier one."))
                .child(self.editor_actions(self.team_dirty(cx), saved, cx)),
        )
    }

    fn render_agent_list(&self, cx: &mut Context<Self>) -> Div {
        let names: Vec<String> = self
            .teams
            .state
            .as_ref()
            .map(|s| s.agents.iter().map(|a| a.name.clone()).collect())
            .unwrap_or_default();
        self.control_card("Agents").child(
            div()
                .flex()
                .flex_wrap()
                .gap(px(8.))
                .children(names.into_iter().map(|name| {
                    let selected = self.teams.agent == Some(Selected::Saved(name.clone()));
                    let pick = name.clone();
                    self.control_choice(format!("teams-agent-{name}"), name, selected, true)
                        .on_click(cx.listener(move |this, _, _, cx| {
                            this.teams.error = None;
                            this.teams.notice = None;
                            this.select_agent(Some(Selected::Saved(pick.clone())), cx);
                        }))
                }))
                .child(
                    self.control_choice(
                        "teams-agent-new",
                        "+ New agent",
                        self.teams.agent == Some(Selected::New),
                        true,
                    )
                    .on_click(cx.listener(|this, _, _, cx| {
                        this.teams.notice = None;
                        this.select_agent(Some(Selected::New), cx);
                    })),
                ),
        )
    }

    fn render_agent_editor(&self, cx: &mut Context<Self>) -> Option<Div> {
        let selected = self.teams.agent.clone()?;
        let drafts = &self.teams.drafts;
        let inputs = &self.teams.inputs;
        let mut models: Vec<String> = self
            .teams
            .state
            .as_ref()
            .map(|s| s.models.clone())
            .unwrap_or_default();
        if !drafts.agent_model.is_empty() && !models.contains(&drafts.agent_model) {
            models.push(drafts.agent_model.clone());
        }
        let model_picker =
            div()
                .flex()
                .flex_wrap()
                .gap(px(6.))
                .children(models.into_iter().enumerate().map(|(index, model)| {
                    let pick = model.clone();
                    let label = model
                        .split_once('/')
                        .map_or(model.as_str(), |(_, m)| m)
                        .to_owned();
                    self.control_choice(
                        format!("teams-model-{index}"),
                        label,
                        drafts.agent_model == model,
                        true,
                    )
                    .on_click(cx.listener(move |this, _, _, cx| {
                        this.teams.drafts.agent_model = pick.clone();
                        cx.notify();
                    }))
                }));
        let saved = matches!(selected, Selected::Saved(_));
        Some(
            self.control_card(if saved { "Agent" } else { "New agent" })
                .child(self.field_row("Name", inputs.agent_name.clone()))
                .child(self.field_row("Description", inputs.agent_description.clone()))
                .child(self.field_row("Model", model_picker))
                .child(
                    self.control_switch("teams-thinking", "Thinking (slower, more careful)", drafts.agent_thinking, true)
                        .on_click(cx.listener(|this, _, _, cx| {
                            this.teams.drafts.agent_thinking = !this.teams.drafts.agent_thinking;
                            cx.notify();
                        })),
                )
                .child(self.field_row("Temperature", inputs.agent_temperature.clone()))
                .child(self.control_note("0 to 2: lower is more predictable. Empty uses the model's default."))
                .child(self.field_row("Step limit", inputs.agent_steps.clone()))
                .child(self.control_note("Most tool steps per turn before the agent must stop and report. Empty means no limit."))
                .child(self.control_note("Prompt"))
                .child(inputs.agent_prompt.clone())
                .child(self.editor_actions(self.agent_dirty(cx), saved, cx)),
        )
    }

    fn render_access(&self, cx: &mut Context<Self>) -> Div {
        let drafts = &self.teams.drafts;
        let mut scopes = div().flex().flex_wrap().gap(px(8.)).child(
            self.control_choice(
                "teams-scope-global",
                "All repos",
                drafts.scope == AccessScope::Global,
                true,
            )
            .on_click(cx.listener(|this, _, _, cx| {
                this.teams.drafts.scope = AccessScope::Global;
                cx.notify();
            })),
        );
        for (index, repo) in drafts.access.repos.iter().enumerate() {
            let label = repo
                .repo
                .rsplit('/')
                .find(|part| !part.is_empty())
                .unwrap_or(&repo.repo)
                .to_owned();
            scopes = scopes.child(
                self.control_choice(
                    format!("teams-scope-{index}"),
                    label,
                    drafts.scope == AccessScope::Repo(index),
                    true,
                )
                .on_click(cx.listener(move |this, _, _, cx| {
                    this.teams.drafts.scope = AccessScope::Repo(index);
                    cx.notify();
                })),
            );
        }
        scopes = scopes.child(
            self.control_choice("teams-scope-add", "+ Repo...", false, true)
                .on_click(cx.listener(|this, _, _, cx| this.browse_teams_folder(true, cx))),
        );
        let scope_note = match drafts.scope {
            AccessScope::Global => "Every sandboxed agent gets these, in any repo.".to_owned(),
            AccessScope::Repo(index) => format!(
                "Agents working on {} also get these.",
                drafts
                    .access
                    .repos
                    .get(index)
                    .map_or("", |r| r.repo.as_str())
            ),
        };
        let grants = drafts.grants();
        let mut rows = div().flex().flex_col().gap(px(6.));
        if grants.is_empty() {
            rows =
                rows.child(self.control_note(
                    "No folders: agents reach only their worktree and the repo's .git.",
                ));
        }
        for (index, (path, mode)) in grants.into_iter().enumerate() {
            let flip = path.clone();
            let remove = path.clone();
            let other = if mode == Mode::ReadOnly {
                Mode::ReadWrite
            } else {
                Mode::ReadOnly
            };
            rows = rows.child(
                div()
                    .flex()
                    .items_center()
                    .gap(px(8.))
                    .child(div().flex_1().min_w_0().truncate().child(path))
                    .child(
                        self.small_button(format!("teams-grant-mode-{index}"), mode.label())
                            .on_click(cx.listener(move |this, _, _, cx| {
                                this.teams.drafts.grant(&flip, other);
                                cx.notify();
                            })),
                    )
                    .child(
                        self.small_button(format!("teams-grant-remove-{index}"), "×")
                            .on_click(cx.listener(move |this, _, _, cx| {
                                this.teams.drafts.revoke(&remove);
                                cx.notify();
                            })),
                    ),
            );
        }
        let add = div()
            .flex()
            .flex_wrap()
            .items_center()
            .gap(px(8.))
            .child(
                div()
                    .flex_1()
                    .min_w(px(200.))
                    .child(self.teams.inputs.access_path.clone()),
            )
            .child(
                self.small_button("teams-grant-browse", "Browse...")
                    .on_click(cx.listener(|this, _, _, cx| this.browse_teams_folder(false, cx))),
            )
            .child(
                self.small_button("teams-grant-add-ro", "Add read-only")
                    .on_click(cx.listener(|this, _, _, cx| this.add_grant(Mode::ReadOnly, cx))),
            )
            .child(
                self.small_button("teams-grant-add-rw", "Add read-write")
                    .on_click(cx.listener(|this, _, _, cx| this.add_grant(Mode::ReadWrite, cx))),
            );
        let dirty = self.access_dirty();
        let actions = div()
            .flex()
            .flex_wrap()
            .items_center()
            .gap(px(8.))
            .child(
                self.teams_button("teams-access-save", "Save", true, dirty)
                    .when(dirty, |b| {
                        b.on_click(cx.listener(|this, _, _, cx| {
                            let access = this.teams.drafts.access.clone();
                            this.apply_teams_change(
                                Change::SaveAccess { access },
                                After::Access,
                                cx,
                            );
                        }))
                    }),
            )
            .child(
                self.teams_button("teams-access-revert", "Revert", false, dirty)
                    .when(dirty, |b| {
                        b.on_click(cx.listener(|this, _, _, cx| {
                            this.teams.error = None;
                            this.reset_access();
                            cx.notify();
                        }))
                    }),
            )
            .child(div().flex_1())
            .when(matches!(drafts.scope, AccessScope::Repo(_)), |row| {
                row.child(
                    self.teams_button("teams-repo-remove", "Remove repo", false, true)
                        .on_click(cx.listener(|this, _, _, cx| {
                            this.teams.drafts.remove_repo();
                            cx.notify();
                        })),
                )
            });
        self.control_card("Folder access")
            .child(scopes)
            .child(self.control_note(scope_note))
            .child(rows)
            .child(add)
            .child(self.control_note(
                "Refused when saved: your home folder itself, ~/.ssh, ~/.gnupg, and Herdr's own settings; \
                 read-write also refuses OpenCode's settings, ~/.local/bin, and folders containing a git repository.",
            ))
            .child(actions)
    }
}
