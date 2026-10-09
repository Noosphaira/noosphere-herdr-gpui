#![allow(clippy::unwrap_used)]

use super::{
    drafts::{AccessScope, Drafts, FieldError, Mode, valid_name},
    model::{self, Access, Change, Grants, Paths},
    *,
};
use crate::settings_window::{Section, SettingsWindow};
use gpui::TestAppContext;
use serde_json::{Map, json};

fn agent(name: &str) -> Agent {
    Agent {
        name: name.into(),
        description: "Builds things".into(),
        model: "spark/fast".into(),
        thinking: false,
        temperature: None,
        steps: None,
        prompt: "You build.".into(),
        extra: Map::new(),
        extra_template_kwargs: Map::new(),
    }
}

fn state() -> State {
    State {
        teams: vec![Team {
            name: "app".into(),
            roles: vec!["coder".into(), "reviewer".into()],
            review_rounds: 2,
            extra: Map::new(),
        }],
        agents: vec![agent("coder"), agent("reviewer")],
        access: Access {
            global: Grants {
                read_only: vec!["~/.config/git/config".into()],
                ..Grants::default()
            },
            repos: Vec::new(),
        },
        models: vec!["spark/big".into(), "spark/fast".into()],
        paths: Paths::default(),
    }
}

#[test]
fn replies_decode_to_state_or_the_refusal() {
    let ok = json!({"ok": true, "state": state()}).to_string();
    assert_eq!(
        model::decode(format!("noise\n{ok}\n").as_bytes()).unwrap(),
        state()
    );
    let refused = br#"{"ok": false, "errors": ["team app: no agent named ghost"]}"#;
    let error = model::decode(refused).unwrap_err();
    assert_eq!(error.to_string(), "team app: no agent named ghost");
    assert!(matches!(
        model::decode(b"command not found"),
        Err(Error::Decode(_))
    ));
}

#[test]
fn changes_are_tagged_and_quoted_for_the_shell() {
    let change = Change::DeleteTeam {
        name: "it's".into(),
    };
    assert_eq!(
        serde_json::to_value(&change).unwrap(),
        json!({"op": "delete_team", "name": "it's"})
    );
    let script = script(Some(&change));
    assert!(
        script
            .contains(r#"printf '%s' '{"op":"delete_team","name":"it'\''s"}' | herdr-teams apply"#)
    );
    assert!(script.contains("command -v herdr-teams"));
}

#[test]
fn names_follow_the_file_rules() {
    assert!(valid_name("ui-designer2"));
    for bad in ["", "Coder", "2coder", "co der", "co/der"] {
        assert!(!valid_name(bad), "{bad}");
    }
}

#[test]
fn agent_fields_parse_or_say_what_is_wrong() {
    let mut drafts = Drafts::default();
    drafts.load_agent(Some(&agent("coder")), "spark/big");
    let built = drafts
        .agent("coder", " Builds things ", "", "", "You build.\n")
        .unwrap();
    assert_eq!(built, agent("coder"));
    let tuned = drafts.agent("coder", "x", "0.3", "40", "p").unwrap();
    assert_eq!((tuned.temperature, tuned.steps), (Some(0.3), Some(40)));
    assert_eq!(
        drafts.agent("coder", "", "2.5", "", "p"),
        Err(FieldError::Temperature)
    );
    assert_eq!(
        drafts.agent("coder", "", "", "0", "p"),
        Err(FieldError::Steps)
    );
    assert_eq!(
        drafts.agent("Coder", "", "", "", "p"),
        Err(FieldError::Name)
    );
    // A new agent starts on the first model, with thinking on.
    drafts.load_agent(None, "spark/big");
    assert_eq!(
        (drafts.agent_model.as_str(), drafts.agent_thinking),
        ("spark/big", true)
    );
}

#[test]
fn roles_move_within_bounds() {
    let mut drafts = Drafts::default();
    drafts.load_team(state().teams.first());
    assert!(!drafts.move_role(0, -1));
    assert!(drafts.move_role(0, 1));
    assert_eq!(drafts.team_roles, ["reviewer", "coder"]);
    assert!(!drafts.move_role(1, 1));
}

#[test]
fn grants_switch_mode_and_follow_the_scope() {
    let mut drafts = Drafts::default();
    drafts.access = state().access;
    assert!(drafts.grant("~/assets", Mode::ReadWrite));
    assert!(drafts.grant("~/.config/git/config", Mode::ReadWrite));
    assert_eq!(
        drafts.grants(),
        [
            ("~/assets".to_owned(), Mode::ReadWrite),
            ("~/.config/git/config".to_owned(), Mode::ReadWrite)
        ]
    );
    drafts.revoke("~/assets");
    drafts.add_repo("/code/app");
    assert_eq!(drafts.scope, AccessScope::Repo(0));
    assert!(drafts.grants().is_empty());
    drafts.grant("~/shared", Mode::ReadOnly);
    assert_eq!(drafts.access.repos[0].read_only, ["~/shared"]);
    drafts.remove_repo();
    assert_eq!(
        (drafts.scope, drafts.access.repos.len()),
        (AccessScope::Global, 0)
    );
}

#[gpui::test]
fn editing_a_team_marks_it_unsaved_until_reverted(cx: &mut TestAppContext) {
    let source = cx.add_window(crate::sidebar::layout_tests::fixture_window);
    let weak = cx.update(|cx| source.update(cx, |_, _, cx| cx.weak_entity()).unwrap());
    let (view, cx) = cx.add_window_view(|_, cx| SettingsWindow::new(weak, cx));
    cx.update(|window, cx| {
        view.update(cx, |view, cx| {
            view.section = Section::Teams;
            view.adopt_teams_state(state(), None, cx);
            view.select_team(Some(Selected::Saved("app".into())), cx);
            assert!(!view.team_dirty(cx));
            view.teams.drafts.move_role(0, 1);
            assert!(view.team_dirty(cx));
            view.select_team(Some(Selected::Saved("app".into())), cx);
            assert!(!view.team_dirty(cx));
            assert!(!view.access_dirty());
            view.teams.drafts.grant("~/assets", Mode::ReadOnly);
            assert!(view.access_dirty());
            view.teams.tab = Tab::Agents;
            view.select_agent(Some(Selected::Saved("coder".into())), cx);
            assert!(!view.agent_dirty(cx));
            view.teams.drafts.agent_thinking = true;
            assert!(view.agent_dirty(cx));
        });
        // Every tab draws.
        for tab in [Tab::Teams, Tab::Agents, Tab::Access] {
            view.update(cx, |view, _| view.teams.tab = tab);
            window.draw(cx).clear(cx);
        }
    });
    assert!(cx.debug_bounds("teams-access-save").is_some());
}

#[gpui::test]
fn manage_teams_opens_settings_on_the_teams_section(cx: &mut TestAppContext) {
    let source = cx.add_window(crate::sidebar::layout_tests::fixture_window);
    cx.update(|cx| {
        let weak = source.update(cx, |_, _, cx| cx.weak_entity()).unwrap();
        crate::settings_window::tests::open_fixture(weak, cx);
        crate::settings_window::show_teams(cx);
        let settings = cx
            .global::<crate::settings_window::SettingsWindowHandle>()
            .window
            .unwrap();
        settings
            .update(cx, |view, _, _| assert_eq!(view.section, Section::Teams))
            .unwrap();
    });
}

#[gpui::test]
fn teams_follow_the_main_windows_scriptable_device(cx: &mut TestAppContext) {
    let source = cx.add_window(crate::sidebar::layout_tests::fixture_window);
    let weak = cx.update(|cx| source.update(cx, |_, _, cx| cx.weak_entity()).unwrap());
    let (view, cx) = cx.add_window_view(|_, cx| SettingsWindow::new(weak, cx));
    let target = |target: herdr_client::ConnectTarget, cx: &mut gpui::VisualTestContext| {
        source
            .update(cx, |window, _, _| {
                window.endpoints[0].connection.target = target
            })
            .unwrap();
        view.read_with(cx, |view, cx| {
            view.teams_device(cx).map(|d| d.host.is_remote())
        })
    };
    // A custom socket cannot run host scripts, so it cannot be edited.
    assert_eq!(
        target(herdr_client::ConnectTarget::Socket("/x.sock".into()), cx),
        None
    );
    assert_eq!(target(herdr_client::ConnectTarget::Local, cx), Some(false));
    let ssh = herdr_client::ConnectTarget::Ssh {
        target: "me@devpc".into(),
        session: "default".into(),
    };
    assert_eq!(target(ssh, cx), Some(true));
}
