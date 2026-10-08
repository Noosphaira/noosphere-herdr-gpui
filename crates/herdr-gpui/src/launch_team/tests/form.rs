use super::*;
use crate::{
    launch_team::{
        Fields, LaunchTeam, Origin, Repo,
        state::{NotReady, Stage},
    },
    search_input::SearchInput,
    teleport::Host,
};
use gpui::{AppContext, TestAppContext};

fn origin() -> Origin {
    Origin {
        endpoint_id: "local".into(),
        endpoint_label: "This Mac".into(),
        host: Host::new(&herdr_client::ConnectTarget::Local).unwrap(),
    }
}

fn launch_team(cx: &mut TestAppContext) -> LaunchTeam {
    let fields = Fields {
        path: cx.new(SearchInput::new),
        branch: cx.new(SearchInput::new),
        task: cx.new(crate::multiline_input::MultilineInput::new),
    };
    let repos = vec![Repo {
        key: "/nonexistent/app/.git".into(),
        label: "app".into(),
        base: Some("feature-a".into()),
    }];
    let mut launch = LaunchTeam::start(origin(), repos, Some(0), fields);
    launch.teams_for_test(vec!["app-team".into()]);
    launch
}

#[gpui::test]
fn the_form_names_what_it_still_needs(cx: &mut TestAppContext) {
    let mut launch = launch_team(cx);
    // A single team is picked for you.
    assert_eq!(launch.team(), Some("app-team"));
    assert_eq!(launch.not_ready("", "", "x"), Some(NotReady::NoBranch));
    assert_eq!(
        launch.not_ready("", "bad..name", "x"),
        Some(NotReady::InvalidBranch)
    );
    assert_eq!(
        launch.not_ready("", "feature-a", "  "),
        Some(NotReady::NoTask)
    );
    assert_eq!(launch.not_ready("", "feature-a", "do it"), None);
    launch.repo = None;
    assert_eq!(
        launch.not_ready("", "feature-a", "do it"),
        Some(NotReady::NoRepo)
    );
    // A typed path stands in for a picked repository, from its own HEAD.
    assert_eq!(
        launch.target(" ~/code/other "),
        Some(("~/code/other", None))
    );
    assert_eq!(launch.not_ready("~/code/other", "feature-a", "do it"), None);
    launch.repo = Some(0);
    assert_eq!(
        launch.target(""),
        Some(("/nonexistent/app/.git", Some("feature-a")))
    );
    launch.selected = None;
    assert_eq!(
        launch.not_ready("", "feature-a", "do it"),
        Some(NotReady::NoTeam)
    );
}

#[gpui::test]
fn a_failed_launch_returns_to_the_form_with_its_error(cx: &mut TestAppContext) {
    let mut launch = launch_team(cx);
    launch.stage = Stage::Launching;
    assert_eq!(
        launch.not_ready("", "feature-a", "do it"),
        Some(NotReady::Launching)
    );
    launch.finish_for_test(Err(Error::Failed {
        step: "worktree".into(),
        message: "branch exists".into(),
        output: String::new(),
    }));
    let (changed, update) = launch.poll();
    assert!(changed);
    assert!(matches!(
        update,
        Some(crate::launch_team::state::Update::Failed)
    ));
    assert_eq!(launch.stage, Stage::Compose);
    assert_eq!(
        launch.error.as_ref().map(ToString::to_string).as_deref(),
        Some("worktree failed: branch exists")
    );
}

#[gpui::test]
fn a_finished_launch_reports_its_workspace(cx: &mut TestAppContext) {
    let mut launch = launch_team(cx);
    launch.stage = Stage::Launching;
    launch.finish_for_test(Ok(Launched {
        workspace_id: "w9".into(),
        worktree: "/w".into(),
        branch: "feature-a".into(),
        panes: [("coder".to_owned(), "w9:p1".to_owned())].into(),
    }));
    let (_, update) = launch.poll();
    assert!(matches!(
        update,
        Some(crate::launch_team::state::Update::Launched(launched)) if launched.workspace_id == "w9"
    ));
}
