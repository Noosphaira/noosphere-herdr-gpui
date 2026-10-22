//! Highlights over terminal panes from the daemon's snapshot, each a tint
//! over the whole pane and a thick border: red over a pane whose agent needs
//! the user (blocked on an approval, or a team role that ran `team blocked`),
//! the accent color over the pane of the agent that is working, or failing
//! that, last changed state, when the tab shows several.

use super::HerdrWindow;
use crate::sidebar::Indicators;
use gpui::{prelude::*, *};
use herdr_client::protocol::{AgentStatus, ClientShellAgent, PaneSurfacePane, SurfaceRect};

/// The pane metadata token a launched team sets while a role is blocked
/// (contrib/local-agents `team blocked`).
const WAITING_TOKEN: &str = "team_waiting";
/// Thick enough to stand out from Herdr's own one-cell border lines.
const FRAME_WIDTH: f32 = 4.;
/// Each highlight's tint over the whole pane, as alpha over its content:
/// strong enough to spot across the screen, light enough to read through.
const ATTENTION_WASH: u32 = 0x33;
const LATEST_WASH: u32 = 0x26;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(super) enum Mark {
    /// The agent waits for the user.
    Attention,
    /// The tab's most recently active agent.
    Latest,
}

/// Each shown pane's mark. A tab with one agent gets no `Latest`: there is
/// nothing to tell apart.
pub(super) fn marks(
    panes: &[PaneSurfacePane],
    agents: &[ClientShellAgent],
    recency: impl Fn(&str) -> u64,
) -> Vec<(SurfaceRect, Mark)> {
    let shown: Vec<(&PaneSurfacePane, &ClientShellAgent)> = panes
        .iter()
        .filter_map(|pane| {
            agents
                .iter()
                .find(|agent| agent.pane_id == pane.pane_id)
                .map(|agent| (pane, agent))
        })
        .collect();
    let waits = |agent: &ClientShellAgent| {
        agent.agent_status == AgentStatus::Blocked
            || agent
                .tokens
                .iter()
                .any(|(name, value)| name == WAITING_TOKEN && !value.is_empty())
    };
    let working = shown
        .iter()
        .any(|(_, agent)| agent.agent_status == AgentStatus::Working);
    let latest = (shown.len() > 1)
        .then(|| {
            shown
                .iter()
                .filter(|(_, agent)| !working || agent.agent_status == AgentStatus::Working)
                .filter(|(pane, _)| recency(&pane.pane_id) > 0)
                .max_by_key(|(pane, _)| recency(&pane.pane_id))
                .map(|(pane, _)| pane.pane_id.as_str())
        })
        .flatten();
    shown
        .iter()
        .filter_map(|(pane, agent)| {
            if waits(agent) {
                Some((pane.rect, Mark::Attention))
            } else if latest == Some(pane.pane_id.as_str()) {
                Some((pane.rect, Mark::Latest))
            } else {
                None
            }
        })
        .collect()
}

impl HerdrWindow {
    /// The marks for `panes`, the frame on screen, placed in the grid's box.
    pub(super) fn render_pane_marks(
        &self,
        panes: &[PaneSurfacePane],
        cell_height: f32,
        window: &Window,
    ) -> Vec<AnyElement> {
        let Some(snapshot) = self.live.snapshot.as_deref() else {
            return Vec::new();
        };
        let light = matches!(
            window.appearance(),
            WindowAppearance::Light | WindowAppearance::VibrantLight
        );
        let attention = Indicators::new(self.settings.shared.as_ref(), light, &self.theme)
            .color(AgentStatus::Blocked);
        let accent = self.theme.primary();
        let cell_width = self.cell_width;
        marks(panes, &snapshot.agents, |pane| {
            self.live.agent_recency.of(pane)
        })
        .into_iter()
        .map(|(rect, mark)| {
            let frame = div()
                .debug_selector(move || format!("pane-mark-{mark:?}-{}-{}", rect.x, rect.y))
                .absolute()
                .left(px(f32::from(rect.x) * cell_width))
                .top(px(f32::from(rect.y) * cell_height))
                .w(px(f32::from(rect.width) * cell_width))
                .h(px(f32::from(rect.height) * cell_height))
                .border(px(FRAME_WIDTH));
            match mark {
                Mark::Attention => frame
                    .border_color(rgb(attention))
                    .bg(rgba((attention << 8) | ATTENTION_WASH)),
                Mark::Latest => frame
                    .border_color(rgb(accent))
                    .bg(rgba((accent << 8) | LATEST_WASH)),
            }
            .into_any_element()
        })
        .collect()
    }
}

#[cfg(test)]
mod tests;
