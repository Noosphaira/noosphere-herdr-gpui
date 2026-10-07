use super::*;
use herdr_client::protocol::{AgentStatus, ClientShellWorktree};

fn workspace(
    id: &str,
    tree: Option<(&str, &str, bool)>,
    branch: Option<&str>,
) -> ClientShellWorkspace {
    ClientShellWorkspace {
        workspace_id: id.into(),
        active_tab_id: format!("{id}:t1"),
        new_workspace_cwd: "/".into(),
        number: 1,
        label: id.into(),
        custom_label: false,
        branch: branch.map(Into::into),
        git_ahead_behind: None,
        tokens: Vec::new(),
        worktree: tree.map(|(key, label, linked)| ClientShellWorktree {
            key: key.into(),
            label: label.into(),
            is_linked_worktree: linked,
        }),
        focused: false,
        agent_status: AgentStatus::Idle,
    }
}

fn host() -> Vec<ClientShellWorkspace> {
    vec![
        workspace("w1", None, None),
        workspace("w2", Some(("/r/app/.git", "app", true)), Some("feature-a")),
        workspace("w3", Some(("/r/app/.git", "app", false)), Some("main")),
        workspace("w4", Some(("/r/lib/.git", "lib", false)), Some("main")),
    ]
}

#[test]
fn each_open_repository_is_offered_once() {
    let repos = open_repos(&host());
    let keys: Vec<_> = repos.iter().map(|repo| repo.key.as_str()).collect();
    assert_eq!(keys, ["/r/app/.git", "/r/lib/.git"]);
    assert!(repos.iter().all(|repo| repo.base.is_none()));
}

#[test]
fn a_main_checkout_picks_its_repository() {
    let workspaces = host();
    let (repos, picked) = choices(&workspaces, repo_of(&workspaces[3]));
    assert_eq!(
        picked.map(|index| repos[index].key.as_str()),
        Some("/r/lib/.git")
    );
    assert_eq!(repos.len(), 2);
}

#[test]
fn a_linked_checkout_picks_its_repository_with_its_branch_as_base() {
    let workspaces = host();
    let (repos, picked) = choices(&workspaces, repo_of(&workspaces[1]));
    let repo = &repos[picked.unwrap()];
    assert_eq!(repo.key, "/r/app/.git");
    assert_eq!(repo.base.as_deref(), Some("feature-a"));
    assert_eq!(repos.len(), 2);
}

#[test]
fn a_workspace_outside_git_picks_nothing() {
    let workspaces = host();
    assert!(repo_of(&workspaces[0]).is_none());
    let (repos, picked) = choices(&workspaces, None);
    assert_eq!((repos.len(), picked), (2, None));
}
