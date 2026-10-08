use crate::{menu::Page, state::ConnectionStatus};
use gpui::{TestAppContext, px, size};

// Host scripts need /bin/sh; the dialog is not offered on other clients.
#[cfg(any(target_os = "linux", target_os = "macos"))]
#[gpui::test]
fn the_sidebar_button_opens_launch_team(cx: &mut TestAppContext) {
    let (view, cx) = cx.add_window_view(crate::sidebar::layout_tests::fixture_window);
    cx.update(|_, cx| {
        view.update(cx, |view, cx| {
            view.live.status = ConnectionStatus::Connected;
            // A custom socket cannot be scripted; this machine can.
            view.endpoints[0].connection.target = herdr_client::ConnectTarget::Local;
            cx.notify();
        })
    });
    // First in the sidebar, above the spaces, at any window width.
    for width in [1200., 640.] {
        cx.simulate_resize(size(px(width), px(600.)));
        cx.update(|window, cx| {
            window.refresh();
            let _ = window.draw(cx);
        });
        let button = cx.debug_bounds("sidebar-launch-team").unwrap();
        let spaces = cx.debug_bounds("spaces-section").unwrap();
        // Its own row, above the spaces section rather than part of it.
        let row = cx.debug_bounds("sidebar-launch-team-row").unwrap();
        assert_eq!(row.bottom(), spaces.top());
        assert!(button.top() >= row.top() && button.bottom() <= row.bottom());
    }
    cx.simulate_resize(size(px(1200.), px(600.)));
    cx.update(|window, cx| {
        window.refresh();
        let _ = window.draw(cx);
    });
    let button = cx.debug_bounds("sidebar-launch-team").unwrap();
    cx.simulate_click(button.center(), gpui::Modifiers::default());
    cx.run_until_parked();
    view.read_with(cx, |view, _| {
        assert_eq!(view.menu.page, Some(Page::LaunchTeam));
        assert!(view.launch_team.is_some());
    });
}
