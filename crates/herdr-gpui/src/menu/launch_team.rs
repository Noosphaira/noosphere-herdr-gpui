//! Opening Launch team from a workspace row or the command palette. The
//! dialog offers every repository open on the host, picks the row's (or the
//! focused workspace's) when there is one, and takes any other repository by
//! path. A linked checkout launches with its branch as the base.

use super::Page;
use crate::{
    HerdrWindow,
    launch_team::{Fields, LaunchTeam, Origin, Repo},
    search_input::SearchInput,
    teleport::host_for,
    window::Flash,
};
use gpui::{AppContext, Context, Window};
use herdr_client::protocol::ClientShellWorkspace;

/// The repositories open on the host, one per Git common directory, main
/// checkouts first so a repository is labelled by its main checkout.
fn open_repos<'a>(workspaces: impl IntoIterator<Item = &'a ClientShellWorkspace>) -> Vec<Repo> {
    let mut trees: Vec<_> = workspaces
        .into_iter()
        .filter_map(|workspace| workspace.worktree.as_ref())
        .collect();
    trees.sort_by_key(|tree| tree.is_linked_worktree);
    let mut repos: Vec<Repo> = Vec::new();
    for tree in trees {
        if repos.iter().all(|repo| repo.key != tree.key) {
            repos.push(Repo {
                key: tree.key.clone(),
                label: tree.label.clone(),
                base: None,
            });
        }
    }
    repos
}

/// The launch target a workspace stands for: its repository, based on its
/// branch when it is a linked checkout.
fn repo_of(workspace: &ClientShellWorkspace) -> Option<Repo> {
    let tree = workspace.worktree.as_ref()?;
    let base = if tree.is_linked_worktree {
        Some(workspace.branch.clone()?)
    } else {
        None
    };
    Some(Repo {
        key: tree.key.clone(),
        label: tree.label.clone(),
        base,
    })
}

/// Every open repository with `context` picked: a linked checkout's entry
/// carries its base, so it replaces the plain one.
fn choices(
    workspaces: &[ClientShellWorkspace],
    context: Option<Repo>,
) -> (Vec<Repo>, Option<usize>) {
    let mut repos = open_repos(workspaces);
    let Some(context) = context else {
        return (repos, None);
    };
    let index = match repos.iter().position(|repo| repo.key == context.key) {
        Some(index) => {
            repos[index] = context;
            index
        }
        None => {
            repos.push(context);
            repos.len() - 1
        }
    };
    (repos, Some(index))
}

impl HerdrWindow {
    /// Whether the selected host can run `herdr-launch` at all.
    fn launch_team_host(&self) -> Option<Origin> {
        // Host scripts need a POSIX client; see `herdr_client::run_script`.
        if !cfg!(any(target_os = "linux", target_os = "macos")) {
            return None;
        }
        let selected = &self.endpoints[self.selected_endpoint];
        let host = host_for(&selected.connection.target).ok()?;
        Some(Origin {
            endpoint_id: selected.id.clone(),
            endpoint_label: selected.label.clone(),
            host,
        })
    }

    /// The workspace menu's row, offered for a Git workspace on a host that
    /// can be scripted.
    pub(super) fn launch_team_item(&self) -> Option<&'static str> {
        let git = self
            .menu
            .target
            .as_ref()
            .is_some_and(|target| target.worktree.is_some());
        (git && self.launch_team_host().is_some()).then_some("Launch team...")
    }

    /// From a workspace row: that workspace's repository is picked.
    pub(super) fn open_launch_team(&mut self, window: &mut Window, cx: &mut Context<Self>) {
        let id = self.menu.target.as_ref().map(|target| target.id.clone());
        self.open_launch_team_with(id.as_deref(), window, cx);
    }

    /// The palette and shortcut path: the focused workspace's repository is
    /// picked when it has one; otherwise the user picks or types one.
    pub(crate) fn open_launch_team_for_focused(
        &mut self,
        window: &mut Window,
        cx: &mut Context<Self>,
    ) {
        if !self.live.status.is_connected() {
            self.show_flash(
                Flash::warning("Not connected, so no team can be launched"),
                cx,
            );
            return;
        }
        let focused = self.live.snapshot.as_ref().and_then(|snapshot| {
            snapshot
                .workspaces
                .iter()
                .find(|workspace| workspace.focused)
                .map(|workspace| workspace.workspace_id.clone())
        });
        self.open_launch_team_with(focused.as_deref(), window, cx);
    }

    fn open_launch_team_with(
        &mut self,
        workspace_id: Option<&str>,
        window: &mut Window,
        cx: &mut Context<Self>,
    ) {
        let Some(origin) = self.launch_team_host() else {
            self.show_flash(
                Flash::warning("Launch team needs a local or SSH host on Linux or macOS"),
                cx,
            );
            return;
        };
        // The palette has no menu open; a workspace row's menu is reused.
        if self.menu.page != Some(Page::Workspace) && !self.open_menu(window, cx) {
            return;
        }
        let workspaces = self
            .live
            .snapshot
            .as_ref()
            .map(|snapshot| snapshot.workspaces.as_slice())
            .unwrap_or_default();
        let context = workspace_id
            .and_then(|id| workspaces.iter().find(|w| w.workspace_id == id))
            .and_then(repo_of);
        let (repos, repo) = choices(workspaces, context);
        let field = |placeholder: &'static str, cx: &mut Context<Self>| {
            let input = cx.new(SearchInput::new);
            input.update(cx, |input, cx| {
                input.set_placeholder(placeholder, cx);
                input.set_appearance(self.config.ui.clone(), self.theme.clone(), cx);
            });
            input
        };
        let fields = Fields {
            path: field("Or a repository path on this host, e.g. ~/code/app", cx),
            branch: field("feature-name", cx),
            task: field("What the team should do", cx),
        };
        // With a repository picked, the branch is what is left to type.
        let first = if repo.is_some() {
            &fields.branch
        } else {
            &fields.path
        };
        window.focus(&first.read(cx).focus.clone(), cx);
        self.menu.page = Some(Page::LaunchTeam);
        self.menu.error = None;
        self.menu.input = None;
        self.launch_team = Some(LaunchTeam::start(origin, repos, repo, fields));
        cx.notify();
    }
}

#[cfg(test)]
#[allow(clippy::unwrap_used)]
mod tests;
