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

// Host scripts need /bin/sh; the dialog is not offered on other clients.
#[cfg(any(target_os = "linux", target_os = "macos"))]
#[gpui::test]
fn the_github_picker_opens_beside_the_dialog(cx: &mut TestAppContext) {
    use crate::launch_team::github::{GithubRepo, Listing, Picker};
    use gpui::AppContext;
    let (view, cx) = cx.add_window_view(crate::sidebar::layout_tests::fixture_window);
    cx.simulate_resize(size(px(1400.), px(800.)));
    cx.update(|window, cx| {
        view.update(cx, |view, cx| {
            view.live.status = ConnectionStatus::Connected;
            view.endpoints[0].connection.target = herdr_client::ConnectTarget::Local;
            view.open_launch_team_for_focused(window, cx);
            // A listed picker, without asking GitHub.
            let search = cx.new(crate::search_input::SearchInput::new);
            let repos = (0..60)
                .map(|i| GithubRepo {
                    full_name: format!("me/repo-{i}"),
                    description: String::new(),
                    private: false,
                    local: None,
                })
                .collect();
            let listing = Listing {
                repos_dir: "/r".into(),
                repos,
            };
            view.launch_team.as_mut().unwrap().github =
                Some(Picker::listed_for_test(search, listing));
        });
        window.refresh();
        let _ = window.draw(cx);
    });
    let dialog = cx.debug_bounds("menu-panel").unwrap();
    let panel = cx.debug_bounds("launch-team-github").unwrap();
    let list = cx.debug_bounds("launch-team-github-list").unwrap();
    // Its own panel to the right of the dialog, the list inside it.
    assert!(panel.left() >= dialog.right(), "{panel:?} {dialog:?}");
    assert!(list.top() >= panel.top() && list.bottom() <= panel.bottom());
    // More rows than fit: the list is what scrolls.
    let last = cx.debug_bounds("launch-team-github-repo-39").unwrap();
    assert!(last.bottom() > list.bottom());
    // The wheel over the list scrolls the list, and not the dialog.
    let first = cx.debug_bounds("launch-team-github-repo-0").unwrap();
    let dialog_before = cx.debug_bounds("menu-panel").unwrap();
    cx.simulate_event(gpui::ScrollWheelEvent {
        position: list.center(),
        delta: gpui::ScrollDelta::Pixels(gpui::point(px(0.), px(-120.))),
        ..Default::default()
    });
    cx.update(|window, cx| {
        window.refresh();
        let _ = window.draw(cx);
    });
    let moved = cx.debug_bounds("launch-team-github-repo-0").unwrap();
    assert!(moved.top() < first.top(), "{moved:?} {first:?}");
    assert_eq!(cx.debug_bounds("menu-panel").unwrap(), dialog_before);
    // The bigger path buttons.
    assert_eq!(cx.debug_bounds("launch-team-github").map(|_| ()), Some(()));
    assert_eq!(
        cx.debug_bounds("launch-team-browse").unwrap().size.width,
        px(34.)
    );
}
