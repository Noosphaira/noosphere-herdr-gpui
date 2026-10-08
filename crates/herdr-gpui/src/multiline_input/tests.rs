#![allow(clippy::unwrap_used)]

use super::*;
use gpui::{TestAppContext, VisualTestContext};

#[test]
fn line_breaks_are_normalized_to_newlines() {
    let mut edit = Editing::default();
    edit.replace(None, "one\r\ntwo\rthree", false, None);
    assert_eq!(edit.text, "one\ntwo\nthree");
    assert_eq!(edit.cursor, edit.text.len());
}

#[test]
fn home_and_end_stay_on_the_cursors_line() {
    let mut edit = Editing::default();
    edit.replace(None, "first\nsecond line\nthird", false, None);
    edit.select_to(9, false);
    assert_eq!((edit.line_start(), edit.line_end()), (6, 17));
}

#[test]
fn composition_selection_maps_through_line_breaks() {
    let mut edit = Editing::default();
    edit.replace(None, "a\r\nb", true, Some(3..4));
    assert_eq!(edit.text, "a\nb");
    assert_eq!(edit.marked, Some(0..3));
    assert_eq!((edit.anchor, edit.cursor), (2, 3));
}

fn field(cx: &mut TestAppContext) -> (gpui::Entity<MultilineInput>, &mut VisualTestContext) {
    let (input, cx) = cx.add_window_view(|_, cx| MultilineInput::new(cx));
    cx.update(|window, cx| window.focus(&input.read(cx).focus.clone(), cx));
    (input, cx)
}

#[gpui::test]
fn shift_enter_starts_a_new_line_and_enter_is_left_to_the_dialog(cx: &mut TestAppContext) {
    let (input, cx) = field(cx);
    cx.simulate_input("first");
    cx.simulate_keystrokes("shift-enter");
    cx.simulate_input("second");
    cx.simulate_keystrokes("enter");
    input.read_with(cx, |input, _| assert_eq!(input.text(), "first\nsecond"));
}

#[gpui::test]
fn long_text_wraps_into_rows(cx: &mut TestAppContext) {
    let (input, cx) = field(cx);
    cx.simulate_resize(size(px(200.), px(400.)));
    cx.simulate_input(&"word ".repeat(60));
    cx.run_until_parked();
    input.read_with(cx, |input, _| {
        assert!(input.rows > 1, "{} rows", input.rows)
    });
}
