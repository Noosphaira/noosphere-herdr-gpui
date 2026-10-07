use crate::{menu::Page, state::ConnectionStatus};
use gpui::{TestAppContext, px, size};

// Host scripts need /bin/sh; the dialog is not offered on other clients.
#[cfg(any(target_os = "linux", target_os = "macos"))]
#[gpui::test]
fn the_title_bar_button_opens_launch_team(cx: &mut TestAppContext) {
    let (view, cx) = cx.add_window_view(crate::sidebar::layout_tests::fixture_window);
    cx.update(|_, cx| {
        view.update(cx, |view, cx| {
            view.live.status = ConnectionStatus::Connected;
            // A custom socket cannot be scripted; this machine can.
            view.endpoints[0].connection.target = herdr_client::ConnectTarget::Local;
            cx.notify();
        })
    });
    // Wide windows spell the button out; narrow ones keep only its icon.
    for width in [1200., 360.] {
        cx.simulate_resize(size(px(width), px(600.)));
        cx.update(|window, cx| {
            window.refresh();
            let _ = window.draw(cx);
        });
        let button = cx.debug_bounds("titlebar-launch-team").unwrap();
        assert!(button.right() <= cx.debug_bounds("titlebar-avatar").unwrap().left());
    }
    cx.simulate_resize(size(px(1200.), px(600.)));
    cx.update(|window, cx| {
        window.refresh();
        let _ = window.draw(cx);
    });
    let button = cx.debug_bounds("titlebar-launch-team").unwrap();
    cx.simulate_click(button.center(), gpui::Modifiers::default());
    cx.run_until_parked();
    view.read_with(cx, |view, _| {
        assert_eq!(view.menu.page, Some(Page::LaunchTeam));
        assert!(view.launch_team.is_some());
    });
}
