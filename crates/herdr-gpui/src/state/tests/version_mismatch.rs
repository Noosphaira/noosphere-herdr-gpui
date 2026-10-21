use super::*;
use herdr_client::VersionMismatch;

#[test]
fn refusal_outlives_its_disconnect_and_clears_on_the_next_welcome() {
    let mut state = LiveState::default();
    let mismatch = VersionMismatch::DaemonOutdated {
        server_version: Some("0.8.2".into()),
    };
    state.apply(ClientEvent::VersionMismatch(mismatch.clone()));
    state.apply(ClientEvent::Disconnected {
        reason: "Herdr server 0.8.2 is too old".into(),
    });
    assert_eq!(state.version_mismatch, Some(mismatch));
    assert_eq!(state.status, ConnectionStatus::Disconnected);
    assert!(!state.only_surface_changed(&LiveState {
        version_mismatch: None,
        ..state.clone()
    }));

    let welcome = serde_json::from_str(include_str!(
        "../../../../herdr-protocol/tests/fixtures/endpoint-welcome-v1.json"
    ))
    .unwrap();
    state.apply(ClientEvent::Connected(welcome));
    assert_eq!(state.version_mismatch, None);
}

#[test]
fn the_daemon_version_lasts_from_its_welcome_to_the_disconnect() {
    let mut state = LiveState::default();
    assert_eq!(state.server_version, None);
    let welcome = serde_json::from_str(include_str!(
        "../../../../herdr-protocol/tests/fixtures/endpoint-welcome-v1.json"
    ))
    .unwrap();
    state.apply(ClientEvent::Connected(welcome));
    assert_eq!(state.server_version.as_deref(), Some("0.8.2"));
    state.apply(ClientEvent::Disconnected {
        reason: "gone".into(),
    });
    assert_eq!(state.server_version, None);
}
