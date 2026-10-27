//! Status bar checks.
use super::*;

pub(super) fn check_status_bar(
    view: &Entity<HerdrWindow>,
    cx: &mut gpui::VisualTestContext,
) -> Result<()> {
    // Exercise the real status bar without starting a daemon connection.
    view.update(cx, |view, cx| {
        view.marked = "composition ".repeat(100);
        view.local_error = Some("long connection error ".repeat(100));
        cx.notify();
    });
    for width in [480., 800.] {
        cx.simulate_resize(size(px(width), px(600.)));
        cx.update(|window, cx| full_draw(window, cx).clear(cx));
        let status = cx.debug_bounds("connection-status").unwrap();
        let version = cx
            .debug_bounds("status-version")
            .context("status version bounds")?;
        assert!(version.size.width > px(0.));
        assert!(cx.debug_bounds("report-issue").is_none());
        assert!(version.right() <= status.right());
        assert!(version.top() >= status.top());
        assert!(version.bottom() <= status.bottom());
        let theme = cx.debug_bounds("status-theme").unwrap();
        let keybinds = cx.debug_bounds("status-keybinds").unwrap();
        assert!(theme.left() >= status.left());
        assert!(theme.right() <= keybinds.left());
        assert!(keybinds.right() <= version.left());
        for button in [theme, keybinds] {
            assert!(button.size.width > px(0.));
            assert!(button.top() >= status.top());
            assert!(button.bottom() <= status.bottom());
        }
    }
    view.read_with(cx, |view, _| {
        assert_eq!(view.version_label(), format!("GUI {}", crate::GUI_VERSION));
    });
    // The version opens About, which names the same version.
    let version = cx
        .debug_bounds("status-version")
        .context("status version bounds")?;
    cx.simulate_click(version.center(), Default::default());
    cx.update(|_, cx| assert!(view.read(cx).menu.page == Some(crate::menu::Page::About)));
    cx.simulate_keystrokes("escape");
    cx.update(|_, cx| assert!(view.read(cx).menu.page.is_none()));
    Ok(())
}
