//! Opening Launch team from a workspace row or the command palette. The
//! repository is the row's own; a linked checkout launches from its
//! repository with its branch as the base.

use super::{Page, WorkspaceTarget, workspace::new_worktree_source};
use crate::{
    HerdrWindow,
    launch_team::{LaunchTeam, Origin},
    search_input::SearchInput,
    teleport::host_for,
    window::Flash,
};
use gpui::{AppContext, Context, Point, Window};

impl HerdrWindow {
    /// The repository the menu's workspace launches into: its Git common
    /// directory, label, and the base for a linked checkout.
    fn launch_team_source(&self) -> Option<(String, String, Option<String>)> {
        let target: &WorkspaceTarget = self.menu.target.as_ref()?;
        let tree = target.worktree.as_ref()?;
        let base = if target.can_delete() {
            Some(target.branch.clone()?)
        } else {
            None
        };
        Some((tree.key.clone(), tree.label.clone(), base))
    }

    /// The workspace menu's row, offered for a Git workspace on a host that
    /// can be scripted.
    pub(super) fn launch_team_item(&self) -> Option<&'static str> {
        // Host scripts need a POSIX client; see `herdr_client::run_script`.
        let scriptable = cfg!(any(target_os = "linux", target_os = "macos"))
            && host_for(&self.endpoints[self.selected_endpoint].connection.target).is_ok();
        (scriptable && self.launch_team_source().is_some()).then_some("Launch team...")
    }

    pub(super) fn open_launch_team(&mut self, window: &mut Window, cx: &mut Context<Self>) {
        let Some((repo, repo_label, base)) = self.launch_team_source() else {
            return;
        };
        let selected = &self.endpoints[self.selected_endpoint];
        let Ok(host) = host_for(&selected.connection.target) else {
            return;
        };
        let origin = Origin {
            endpoint_id: selected.id.clone(),
            endpoint_label: selected.label.clone(),
            host,
            repo,
            repo_label,
            base,
        };
        let field = |placeholder: &'static str, cx: &mut Context<Self>| {
            let input = cx.new(SearchInput::new);
            input.update(cx, |input, cx| {
                input.set_placeholder(placeholder, cx);
                input.set_appearance(self.config.ui.clone(), self.theme.clone(), cx);
            });
            input
        };
        let branch = field("feature-name", cx);
        let task = field("What the team should do", cx);
        window.focus(&branch.read(cx).focus.clone(), cx);
        self.menu.page = Some(Page::LaunchTeam);
        self.menu.error = None;
        self.menu.input = None;
        self.launch_team = Some(LaunchTeam::start(origin, branch, task));
        cx.notify();
    }

    /// The palette and shortcut path: the focused workspace's repository,
    /// or a flash saying why there is none.
    pub(crate) fn open_launch_team_for_focused(
        &mut self,
        window: &mut Window,
        cx: &mut Context<Self>,
    ) {
        let source = match &self.live.snapshot {
            Some(snapshot) if self.live.status.is_connected() => new_worktree_source(snapshot),
            _ => Err(super::workspace::NewWorktreeUnavailable::Disconnected),
        };
        let id = match source {
            Ok(id) => id,
            Err(reason) => {
                self.show_flash(Flash::warning(reason.message()), cx);
                return;
            }
        };
        self.open_workspace_menu(&id, Point::default(), window, cx);
        if self.menu.page != Some(Page::Workspace) {
            return;
        }
        if self.launch_team_item().is_none() {
            self.dismiss_menu(window, cx);
            self.show_flash(
                Flash::warning("Launch team needs a Git workspace on a local or SSH host"),
                cx,
            );
            return;
        }
        self.open_launch_team(window, cx);
    }
}
