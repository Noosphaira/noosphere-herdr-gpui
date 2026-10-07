//! The Launch team dialog: pick a team, name the branch, describe the task,
//! then follow the workspace `herdr-launch` created. Closing the dialog never
//! stops a launch; its outcome then arrives as a flash.

use super::state::{Stage, Teams, Update};
use crate::{
    HerdrWindow,
    menu::Page,
    progress::{self, Progress as Bar},
    teleport::Follow,
    window::Flash,
};
use gpui::{prelude::*, *};

impl HerdrWindow {
    pub(crate) fn poll_launch_team(&mut self, window: &mut Window, cx: &mut Context<Self>) {
        let open = self.menu.page == Some(Page::LaunchTeam);
        let Some(launch) = &mut self.launch_team else {
            return;
        };
        if !open {
            // Nothing was created yet, so there is nothing to come back to.
            if !launch.launching() {
                self.launch_team = None;
                return;
            }
            launch.stop_lookup();
        }
        let (changed, update) = launch.poll();
        match update {
            Some(Update::Launched(launched)) => {
                let endpoint = launch.origin.endpoint_id.clone();
                let roles = launched.panes.len();
                self.launch_team = None;
                if open {
                    self.dismiss_menu(window, cx);
                }
                self.show_flash(
                    Flash::success(format!("Launched {roles} agent(s) on {}", launched.branch)),
                    cx,
                );
                self.teleport_follow = Some(Follow::new(endpoint, launched.workspace_id));
            }
            Some(Update::Failed) if !open => {
                let message = launch
                    .error
                    .as_ref()
                    .map_or_else(|| "Team launch failed".to_owned(), ToString::to_string);
                self.launch_team = None;
                self.show_flash(Flash::warning(message), cx);
            }
            Some(Update::Failed) | None => {
                if changed {
                    cx.notify();
                }
            }
        }
    }

    pub(crate) fn launch_team_key(
        &mut self,
        event: &KeyDownEvent,
        window: &mut Window,
        cx: &mut Context<Self>,
    ) {
        let Some(launch) = &self.launch_team else {
            return;
        };
        let composing = [&launch.path, &launch.branch, &launch.task]
            .iter()
            .any(|field| field.read(cx).is_composing());
        // The fields edit themselves; only these keys belong to the dialog.
        if composing || !matches!(event.keystroke.key.as_str(), "escape" | "enter" | "tab") {
            return;
        }
        cx.stop_propagation();
        window.prevent_default();
        match event.keystroke.key.as_str() {
            "escape" => self.dismiss_menu(window, cx),
            "enter" => self.submit_launch_team(cx),
            _ => self.cycle_launch_team_field(window, cx),
        }
    }

    /// Tab moves through the text fields: path, branch, task, and around.
    fn cycle_launch_team_field(&mut self, window: &mut Window, cx: &mut Context<Self>) {
        let Some(launch) = &self.launch_team else {
            return;
        };
        let fields =
            [&launch.path, &launch.branch, &launch.task].map(|field| field.read(cx).focus.clone());
        let next = fields
            .iter()
            .position(|focus| focus.is_focused(window))
            .map_or(1, |index| (index + 1) % fields.len());
        window.focus(&fields[next], cx);
    }

    fn submit_launch_team(&mut self, cx: &mut Context<Self>) {
        let Some(launch) = &mut self.launch_team else {
            return;
        };
        let path = launch.path.read(cx).text().to_owned();
        let branch = launch.branch.read(cx).text().to_owned();
        let task = launch.task.read(cx).text().to_owned();
        if launch.launch(&path, &branch, &task) {
            cx.notify();
        }
    }

    pub(crate) fn render_launch_team(&self, cx: &mut Context<Self>) -> Div {
        let Some(launch) = &self.launch_team else {
            return div();
        };
        let theme = &self.theme;
        let font = &self.config.ui;
        let muted = rgb(theme.muted);
        let danger = crate::menu::danger(theme);
        let line = |text: String| div().min_w_0().child(text);
        // Captions share a column so every field starts at the same edge.
        let row = |caption: &'static str, field: AnyElement| {
            div()
                .flex()
                .items_center()
                .gap(px(10.))
                .child(
                    div()
                        .flex_none()
                        .w(px(font.size * 4.5))
                        .text_color(muted)
                        .child(caption),
                )
                .child(div().flex_1().min_w_0().child(field))
        };

        let chip = |id: &'static str, index: usize, label: String, picked: bool| {
            div()
                .id((id, index))
                .debug_selector(move || format!("{id}-{index}"))
                .px(px(10.))
                .py(px(3.))
                .rounded(px(crate::config::corners::CONTROL))
                .border_1()
                .border_color(if picked {
                    rgb(theme.foreground)
                } else {
                    rgb(theme.active)
                })
                .when(picked, |chip| chip.bg(rgb(theme.active)))
                .cursor_pointer()
                .hover(|chip| chip.bg(rgb(theme.active)))
                .child(label)
        };
        let path_text = launch.path.read(cx).text().to_owned();
        // A typed path replaces the picked repository, so the pick dims.
        let typed = !path_text.trim().is_empty();
        let repos = div()
            .flex()
            .flex_col()
            .gap(px(6.))
            .when(!launch.repos.is_empty(), |column| {
                column.child(div().flex().flex_wrap().gap(px(6.)).children(
                    launch.repos.iter().enumerate().map(|(index, repo)| {
                        let label = match &repo.base {
                            Some(base) => format!("{} (from {base})", repo.label),
                            None => repo.label.clone(),
                        };
                        chip(
                            "launch-team-repo",
                            index,
                            label,
                            !typed && launch.repo == Some(index),
                        )
                        .when(typed, |chip| chip.opacity(0.5))
                        .on_click(cx.listener(move |this, _, _, cx| {
                            cx.stop_propagation();
                            if let Some(launch) = &mut this.launch_team
                                && !launch.launching()
                            {
                                launch.repo = Some(index);
                                launch.path.update(cx, |path, cx| path.clear(cx));
                                cx.notify();
                            }
                        }))
                    }),
                ))
            })
            .child(
                div()
                    .debug_selector(|| "launch-team-path".into())
                    .child(launch.path.clone()),
            )
            .into_any_element();

        let teams: AnyElement = match &launch.teams {
            Teams::Loading => progress::bar(
                "launch-team-progress",
                Bar::Busy,
                crate::menu::accent(theme).into(),
                rgb(theme.active).into(),
            )
            .into_any_element(),
            Teams::Failed(error) => line(error.clone())
                .debug_selector(|| "launch-team-teams-error".into())
                .text_color(danger)
                .into_any_element(),
            Teams::Listed(teams) if teams.is_empty() => {
                line("No teams yet: add one under ~/.config/herdr-launch/teams/".into())
                    .text_color(danger)
                    .into_any_element()
            }
            Teams::Listed(teams) => div()
                .flex()
                .flex_wrap()
                .gap(px(6.))
                .children(teams.iter().enumerate().map(|(index, team)| {
                    chip(
                        "launch-team-team",
                        index,
                        team.clone(),
                        launch.selected == Some(index),
                    )
                    .on_click(cx.listener(move |this, _, _, cx| {
                        cx.stop_propagation();
                        if let Some(launch) = &mut this.launch_team
                            && !launch.launching()
                        {
                            launch.selected = Some(index);
                            cx.notify();
                        }
                    }))
                }))
                .into_any_element(),
        };

        let branch_text = launch.branch.read(cx).text().to_owned();
        let task_text = launch.task.read(cx).text().to_owned();
        let not_ready = launch.not_ready(&path_text, &branch_text, &task_text);
        let base = launch
            .target(&path_text)
            .and_then(|(_, base)| base)
            .unwrap_or("HEAD");
        let mut body = div()
            .flex()
            .flex_col()
            .gap(px(10.))
            .px(px(16.))
            .py(px(12.))
            .child(row("Repo", repos))
            .child(row("Team", teams))
            .child(row(
                "Branch",
                div()
                    .debug_selector(|| "launch-team-branch".into())
                    .child(launch.branch.clone())
                    .into_any_element(),
            ))
            .child(row(
                "Task",
                div()
                    .debug_selector(|| "launch-team-task".into())
                    .child(launch.task.clone())
                    .into_any_element(),
            ))
            .child(
                line(format!(
                    "Creates the branch from {base} in a new worktree, then starts one sandboxed agent per role and gives each the task."
                ))
                .text_color(muted),
            );
        if launch.stage == Stage::Launching {
            body = body
                .child(progress::bar(
                    "launch-team-progress",
                    Bar::Busy,
                    crate::menu::accent(theme).into(),
                    rgb(theme.active).into(),
                ))
                .child(
                    line("Launching... closing this dialog does not stop it.".into())
                        .text_color(muted),
                );
        }
        if let Some(error) = &launch.error {
            body = body.child(
                line(error.to_string())
                    .debug_selector(|| "launch-team-error".into())
                    .text_color(danger),
            );
            if let Some(output) = error.output() {
                body = body.child(
                    div()
                        .debug_selector(|| "launch-team-output".into())
                        .rounded(px(crate::config::corners::CONTROL))
                        .bg(rgb(theme.active))
                        .px(px(10.))
                        .py(px(6.))
                        .text_size(px(font.size * 0.9))
                        .children(output.lines().take(20).map(|text| line(text.to_owned()))),
                );
            }
        }

        let armed = not_ready.is_none();
        let button = |id: &'static str| {
            div()
                .id(id)
                .debug_selector(move || id.into())
                .px(px(12.))
                .py(px(6.))
                .rounded(px(crate::config::corners::CONTROL))
                .border_1()
                .cursor_pointer()
        };
        let primary = match not_ready {
            Some(reason) if launch.launching() => reason.message().to_owned(),
            _ => "Launch team".to_owned(),
        };
        div()
            .flex()
            .flex_col()
            .child(
                div()
                    .flex()
                    .items_center()
                    .gap(px(10.))
                    .px(px(16.))
                    .py(px(12.))
                    .border_b_1()
                    .border_color(rgb(theme.active))
                    .child(
                        svg()
                            .path("icons/agent-opencode.svg")
                            .size(px(16.))
                            .flex_none()
                            .text_color(muted),
                    )
                    .child(
                        div()
                            .flex()
                            .flex_col()
                            .flex_1()
                            .min_w_0()
                            .child(
                                div()
                                    .text_size(px(font.size * 1.35))
                                    .font_weight(FontWeight::SEMIBOLD)
                                    .child("Launch team"),
                            )
                            .child(
                                div()
                                    .truncate()
                                    .text_color(muted)
                                    .child(format!("on {}", launch.origin.endpoint_label)),
                            ),
                    ),
            )
            .child(
                div()
                    .id("launch-team-body")
                    .max_h(px(460.))
                    .overflow_y_scroll()
                    .child(body),
            )
            .child(
                div()
                    .flex()
                    .items_center()
                    .justify_end()
                    .gap(px(8.))
                    .px(px(16.))
                    .py(px(12.))
                    .border_t_1()
                    .border_color(rgb(theme.active))
                    .children(not_ready.filter(|_| !launch.launching()).map(|reason| {
                        div()
                            .flex_1()
                            .min_w_0()
                            .truncate()
                            .debug_selector(|| "launch-team-hint".into())
                            .text_color(muted)
                            .child(reason.message())
                    }))
                    .child(
                        button("launch-team-cancel")
                            .border_color(rgb(theme.active))
                            .hover(|button| button.bg(rgb(theme.active)))
                            .child(if launch.launching() { "Hide" } else { "Cancel" })
                            .on_click(cx.listener(|this, _, window, cx| {
                                cx.stop_propagation();
                                this.dismiss_menu(window, cx);
                            })),
                    )
                    .child(
                        button("launch-team-submit")
                            .border_color(if armed {
                                rgb(theme.foreground)
                            } else {
                                rgb(theme.active)
                            })
                            .text_color(if armed { rgb(theme.foreground) } else { muted })
                            .child(primary)
                            .when(!armed, |button| {
                                button.opacity(0.4).cursor(CursorStyle::OperationNotAllowed)
                            })
                            .when(armed, |button| {
                                button.bg(rgb(theme.active)).on_click(cx.listener(
                                    |this, _, _, cx| {
                                        cx.stop_propagation();
                                        this.submit_launch_team(cx);
                                    },
                                ))
                            }),
                    ),
            )
    }
}
