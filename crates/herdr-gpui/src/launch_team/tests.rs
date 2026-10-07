#![allow(clippy::unwrap_used)]

use super::{
    error::Error,
    job::{self, Launched, Request},
};

mod form;

fn request() -> Request {
    Request {
        team: "app-team".into(),
        repo: "/home/me/code/app/.git".into(),
        branch: "feature-login".into(),
        task: "Add a login screen. Don't break 'auth'; use $HOME\nnot `pwd`.".into(),
        base: None,
    }
}

#[test]
fn script_quotes_every_value_and_keeps_the_report_on_failure() {
    let script = job::script(Some("work"), &request());
    let command = script
        .lines()
        .find(|line| line.starts_with("'herdr-launch'"))
        .unwrap();
    assert!(command.starts_with("'herdr-launch' '--json' '--session' 'work' '--team' 'app-team'"));
    // Quotes are closed and escaped, so `$`, backticks and newlines stay text.
    assert!(
        script.contains(
            "'Add a login screen. Don'\\''t break '\\''auth'\\''; use $HOME\nnot `pwd`.'"
        )
    );
    assert!(script.ends_with(" || true\n"));
    assert!(script.contains("command -v herdr-launch"));
}

#[test]
fn script_passes_base_and_omits_the_default_session() {
    let script = job::script(
        None,
        &Request {
            base: Some("feature/x".into()),
            ..request()
        },
    );
    assert!(!script.contains("--session"));
    assert!(script.contains("'--base' 'feature/x'"));
}

#[test]
fn decode_reads_the_last_line_as_the_report() {
    let output = b"noise\n{\"ok\": true, \"workspace_id\": \"w3\", \"worktree\": \"/w\", \"branch\": \"b\", \"panes\": {\"coder\": \"w3:p2\", \"reviewer\": \"w3:p3\"}}\n";
    let launched: Launched = job::decode(output).unwrap();
    assert_eq!(launched.workspace_id, "w3");
    assert_eq!(
        launched.panes.get("coder").map(String::as_str),
        Some("w3:p2")
    );
}

#[test]
fn decode_turns_a_reported_failure_into_its_step() {
    let output = br#"{"ok": false, "step": "validate", "error": "branch 'x' already exists", "output": "details"}"#;
    let error = job::decode::<Launched>(output).unwrap_err();
    assert!(matches!(&error, Error::Failed { step, .. } if step == "validate"));
    assert_eq!(
        error.to_string(),
        "validate failed: branch 'x' already exists"
    );
    assert_eq!(error.output(), Some("details"));
}

#[test]
fn decode_rejects_unexpected_output() {
    assert!(matches!(
        job::decode::<Launched>(b"command not found"),
        Err(Error::Decode(_))
    ));
}
