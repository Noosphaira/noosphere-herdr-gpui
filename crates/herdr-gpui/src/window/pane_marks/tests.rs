#![allow(clippy::unwrap_used)]

use super::{Mark, marks};
use herdr_client::protocol::{AgentStatus, ClientShellAgent, PaneSurfacePane, SurfaceRect};
use std::collections::HashMap;

fn rect(x: u16) -> SurfaceRect {
    SurfaceRect {
        x,
        y: 0,
        width: 10,
        height: 5,
    }
}

fn pane(id: &str, x: u16) -> PaneSurfacePane {
    PaneSurfacePane {
        pane_id: id.into(),
        content_revision: 1,
        rect: rect(x),
        inner_rect: rect(x),
        scrollbar_rect: None,
        scroll: None,
        focused: false,
        mouse_reporting: false,
        sgr_pixel_mouse: false,
        alternate_screen_active: false,
        pixel_width: 0,
        pixel_height: 0,
    }
}

fn agent(id: &str, status: AgentStatus) -> ClientShellAgent {
    ClientShellAgent {
        pane_id: id.into(),
        workspace_id: "w1".into(),
        tab_id: "w1:t1".into(),
        name: None,
        display_agent: None,
        agent: Some("pi".into()),
        title: None,
        terminal_title: None,
        terminal_title_stripped: None,
        agent_status: status,
        state_change_seq: 1,
        state_labels: Vec::new(),
        tokens: Vec::new(),
        focused: false,
    }
}

fn team() -> Vec<PaneSurfacePane> {
    vec![pane("p1", 0), pane("p2", 10), pane("p3", 20)]
}

fn recency(stamps: &[(&str, u64)]) -> impl Fn(&str) -> u64 {
    let stamps: HashMap<String, u64> = stamps.iter().map(|(id, s)| ((*id).into(), *s)).collect();
    move |pane| stamps.get(pane).copied().unwrap_or_default()
}

#[test]
fn the_working_agent_is_latest_even_if_another_changed_since() {
    let agents = [
        agent("p1", AgentStatus::Idle),
        agent("p2", AgentStatus::Working),
        agent("p3", AgentStatus::Idle),
    ];
    let marks = marks(
        &team(),
        &agents,
        recency(&[("p1", 1), ("p2", 2), ("p3", 3)]),
    );
    assert_eq!(marks, vec![(rect(10), Mark::Latest)]);
}

#[test]
fn with_nobody_working_the_last_change_is_latest() {
    let agents = [
        agent("p1", AgentStatus::Idle),
        agent("p2", AgentStatus::Idle),
        agent("p3", AgentStatus::Done),
    ];
    let marks = marks(
        &team(),
        &agents,
        recency(&[("p1", 5), ("p2", 2), ("p3", 3)]),
    );
    assert_eq!(marks, vec![(rect(0), Mark::Latest)]);
}

#[test]
fn blocked_and_waiting_agents_need_attention() {
    let mut waiting = agent("p3", AgentStatus::Idle);
    waiting.tokens = vec![("team_waiting".into(), "1".into())];
    let agents = [
        agent("p1", AgentStatus::Blocked),
        agent("p2", AgentStatus::Working),
        waiting,
    ];
    let marks = marks(&team(), &agents, recency(&[("p2", 2)]));
    assert_eq!(
        marks,
        vec![
            (rect(0), Mark::Attention),
            (rect(10), Mark::Latest),
            (rect(20), Mark::Attention),
        ]
    );
}

#[test]
fn a_waiting_agent_is_not_also_latest() {
    let mut waiting = agent("p1", AgentStatus::Idle);
    waiting.tokens = vec![("team_waiting".into(), "1".into())];
    let agents = [waiting, agent("p2", AgentStatus::Idle)];
    let marks = marks(&team(), &agents, recency(&[("p1", 9), ("p2", 1)]));
    assert_eq!(marks, vec![(rect(0), Mark::Attention)]);
}

#[test]
fn one_agent_or_plain_shells_get_no_latest_mark() {
    let marks_one = marks(
        &team(),
        &[agent("p1", AgentStatus::Working)],
        recency(&[("p1", 1)]),
    );
    assert_eq!(marks_one, Vec::new());
    let unseen = [
        agent("p1", AgentStatus::Idle),
        agent("p2", AgentStatus::Idle),
    ];
    assert_eq!(marks(&team(), &unseen, recency(&[])), Vec::new());
    // Agents on panes the frame doesn't show (another tab, a zoomed pane) don't count.
    let elsewhere = [
        agent("p1", AgentStatus::Working),
        agent("p9", AgentStatus::Idle),
    ];
    assert_eq!(
        marks(&team(), &elsewhere, recency(&[("p1", 1), ("p9", 2)])),
        Vec::new()
    );
}
