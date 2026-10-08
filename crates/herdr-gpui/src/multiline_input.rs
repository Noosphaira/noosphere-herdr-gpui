//! A wrapping, multi-line, native-IME-aware text field for longer prose such
//! as a task description. Shift-Enter starts a new line; plain Enter, Escape,
//! and Tab are left to the surrounding dialog, as in [`SearchInput`].
//!
//! [`SearchInput`]: crate::search_input::SearchInput
use std::ops::Range;

use gpui::{
    App, Bounds, ClipboardItem, ContentMask, Context, CursorStyle, EntityInputHandler, FocusHandle,
    Focusable, KeyDownEvent, MouseButton, Pixels, Point, TextAlign, TextRun, UTF16Selection,
    UnderlineStyle, Window, WrappedLine, canvas, div, fill, point, prelude::*, px, rgb, size,
};

use crate::config::{Config, FontConfig, Theme};
use crate::fonts::StyledFont;
use crate::search_input::{byte_range, from_utf16, to_utf16};
use crate::{actions, input::ViewInputHandler};

/// Rows shown before the field scrolls.
const MIN_ROWS: usize = 3;
const MAX_ROWS: usize = 8;

pub struct MultilineInput {
    pub focus: FocusHandle,
    placeholder: String,
    edit: Editing,
    font: FontConfig,
    theme: Theme,
    /// One shaped paragraph per `\n`-separated line, with its byte start.
    layout: Vec<(usize, WrappedLine)>,
    bounds: Option<Bounds<Pixels>>,
    /// Wrapped rows in the last layout, which sizes the field.
    rows: usize,
    scroll: Pixels,
    selecting: bool,
}

#[derive(Default)]
struct Editing {
    text: String,
    anchor: usize,
    cursor: usize,
    marked: Option<Range<usize>>,
}

/// Carriage returns are dropped so `\n` is the only line break.
fn normalize(text: &str) -> String {
    text.replace("\r\n", "\n").replace('\r', "\n")
}

impl Editing {
    fn selection(&self) -> Range<usize> {
        self.anchor.min(self.cursor)..self.anchor.max(self.cursor)
    }

    fn select_to(&mut self, offset: usize, extend: bool) {
        self.cursor = offset;
        if !extend {
            self.anchor = offset;
        }
    }

    fn previous(&self) -> usize {
        self.text[..self.cursor]
            .char_indices()
            .next_back()
            .map_or(0, |(i, _)| i)
    }

    fn next(&self) -> usize {
        self.text[self.cursor..]
            .chars()
            .next()
            .map_or(self.cursor, |ch| self.cursor + ch.len_utf8())
    }

    fn line_start(&self) -> usize {
        self.text[..self.cursor].rfind('\n').map_or(0, |i| i + 1)
    }

    fn line_end(&self) -> usize {
        self.text[self.cursor..]
            .find('\n')
            .map_or(self.text.len(), |i| self.cursor + i)
    }

    fn replace(
        &mut self,
        range: Option<Range<usize>>,
        text: &str,
        composing: bool,
        selection: Option<Range<usize>>,
    ) -> bool {
        let range = range
            .map(|r| byte_range(&self.text, r))
            .or_else(|| self.marked.clone())
            .unwrap_or_else(|| self.selection());
        let inserted = normalize(text);
        let changed = self.text[range.clone()] != inserted;
        self.text.replace_range(range.clone(), &inserted);
        self.marked = (composing && !inserted.is_empty())
            .then_some(range.start..range.start + inserted.len());
        self.cursor = range.start + inserted.len();
        self.anchor = self.cursor;
        if composing && let Some(selection) = selection {
            // IME selection is relative to the supplied text, not the buffer.
            self.anchor = range.start + normalize(&text[..from_utf16(text, selection.start)]).len();
            self.cursor = range.start + normalize(&text[..from_utf16(text, selection.end)]).len();
        }
        changed
    }
}

impl MultilineInput {
    pub fn new(cx: &mut Context<Self>) -> Self {
        Self {
            focus: cx.focus_handle(),
            placeholder: String::new(),
            edit: Editing::default(),
            font: Config::default().ui,
            theme: Theme::default(),
            layout: Vec::new(),
            bounds: None,
            rows: 1,
            scroll: px(0.),
            selecting: false,
        }
    }

    pub fn text(&self) -> &str {
        &self.edit.text
    }

    pub fn set_placeholder(&mut self, value: &str, cx: &mut Context<Self>) {
        self.placeholder = value.into();
        cx.notify();
    }

    pub fn is_composing(&self) -> bool {
        self.edit.marked.is_some()
    }

    pub fn set_appearance(&mut self, font: FontConfig, theme: Theme, cx: &mut Context<Self>) {
        self.font = font;
        self.theme = theme;
        cx.notify();
    }

    fn line_height(&self) -> Pixels {
        px(self.font.line_height())
    }

    /// Where `offset` sits, relative to the text's top-left corner.
    fn position(&self, offset: usize) -> Point<Pixels> {
        if self.edit.text.is_empty() {
            return Point::default();
        }
        let height = self.line_height();
        let mut top = px(0.);
        for (start, line) in &self.layout {
            if offset <= start + line.len() {
                let local = line
                    .position_for_index(offset - start, height)
                    .unwrap_or_default();
                return point(local.x, top + local.y);
            }
            top += line.size(height).height;
        }
        point(px(0.), top)
    }

    /// The text offset closest to `position`, relative to the text's corner.
    fn index_at(&self, position: Point<Pixels>) -> usize {
        if self.edit.text.is_empty() {
            return 0;
        }
        let height = self.line_height();
        let mut top = px(0.);
        for (index, (start, line)) in self.layout.iter().enumerate() {
            let bottom = top + line.size(height).height;
            if position.y < bottom || index + 1 == self.layout.len() {
                let local = point(
                    position.x,
                    (position.y - top).max(px(0.)).min(bottom - top - px(1.)),
                );
                let offset = line
                    .closest_index_for_position(local, height)
                    .unwrap_or_else(|end| end);
                return start + offset.min(line.len());
            }
            top = bottom;
        }
        0
    }

    fn mouse_index(&self, position: Point<Pixels>) -> usize {
        match self.bounds {
            Some(bounds) if !self.edit.text.is_empty() => self.index_at(point(
                position.x - bounds.left(),
                position.y - bounds.top() + self.scroll,
            )),
            _ => 0,
        }
    }

    /// Up or down one visual row from the cursor, keeping its column.
    fn vertical(&self, rows: f32) -> usize {
        let at = self.position(self.edit.cursor);
        let target = at.y + self.line_height() * rows;
        if target < px(0.) {
            return 0;
        }
        let total = self.line_height() * self.rows as f32;
        if target >= total {
            return self.edit.text.len();
        }
        self.index_at(point(at.x, target + self.line_height() / 2.))
    }

    fn did_edit(&mut self, cx: &mut Context<Self>) {
        cx.notify();
    }

    /// Edits from this field's own keys: Shift-Enter, deletion, and paste.
    fn insert(&mut self, text: &str, cx: &mut Context<Self>) {
        self.edit.replace(None, text, false, None);
        self.did_edit(cx);
    }

    fn key_down(&mut self, event: &KeyDownEvent, window: &mut Window, cx: &mut Context<Self>) {
        if self.is_composing() {
            return;
        }
        let key = event.keystroke.key.as_str();
        let modifiers = event.keystroke.modifiers;
        if key == "enter" && modifiers.shift && !modifiers.platform && !modifiers.control {
            self.insert("\n", cx);
        } else if matches!(key, "enter" | "escape" | "tab") {
            return;
        } else if modifiers.platform && !modifiers.control && !modifiers.alt {
            match key {
                "left" | "right" => {
                    let offset = if key == "left" {
                        self.edit.line_start()
                    } else {
                        self.edit.line_end()
                    };
                    self.edit.select_to(offset, modifiers.shift);
                }
                "up" | "down" => {
                    let offset = if key == "up" { 0 } else { self.edit.text.len() };
                    self.edit.select_to(offset, modifiers.shift);
                }
                "backspace" if !modifiers.shift => {
                    if self.edit.selection().is_empty() {
                        let start = self.edit.line_start();
                        self.edit.select_to(start, true);
                    }
                    self.insert("", cx);
                }
                _ if modifiers.shift => return,
                "a" => {
                    self.edit.anchor = 0;
                    self.edit.cursor = self.edit.text.len();
                }
                "c" | "x" => {
                    let selection = self.edit.selection();
                    if !selection.is_empty() {
                        cx.write_to_clipboard(ClipboardItem::new_string(
                            self.edit.text[selection].into(),
                        ));
                        if key == "x" {
                            self.insert("", cx);
                        }
                    }
                }
                "v" => {
                    if let Some(text) = cx.read_from_clipboard().and_then(|item| item.text()) {
                        self.insert(&text, cx);
                    }
                }
                _ => return,
            }
        } else if !modifiers.platform && !modifiers.control && !modifiers.alt {
            match key {
                "backspace" | "delete" => {
                    if self.edit.selection().is_empty() {
                        let offset = if key == "backspace" {
                            self.edit.previous()
                        } else {
                            self.edit.next()
                        };
                        self.edit.select_to(offset, true);
                    }
                    self.insert("", cx);
                }
                "left" | "right" | "home" | "end" | "up" | "down" => {
                    let selection = self.edit.selection();
                    let offset = match key {
                        "home" => self.edit.line_start(),
                        "end" => self.edit.line_end(),
                        "up" => self.vertical(-1.),
                        "down" => self.vertical(1.),
                        "left" if !modifiers.shift && !selection.is_empty() => selection.start,
                        "right" if !modifiers.shift && !selection.is_empty() => selection.end,
                        "left" => self.edit.previous(),
                        _ => self.edit.next(),
                    };
                    self.edit.select_to(offset, modifiers.shift);
                }
                _ => return,
            }
        } else {
            return;
        }
        cx.stop_propagation();
        window.prevent_default();
        cx.notify();
    }
}

impl Focusable for MultilineInput {
    fn focus_handle(&self, _: &App) -> FocusHandle {
        self.focus.clone()
    }
}

impl EntityInputHandler for MultilineInput {
    fn text_for_range(
        &mut self,
        range: Range<usize>,
        actual: &mut Option<Range<usize>>,
        _: &mut Window,
        _: &mut Context<Self>,
    ) -> Option<String> {
        let range = byte_range(self.text(), range);
        *actual = Some(to_utf16(self.text(), range.start)..to_utf16(self.text(), range.end));
        Some(self.edit.text[range].into())
    }

    fn selected_text_range(
        &mut self,
        _: bool,
        _: &mut Window,
        _: &mut Context<Self>,
    ) -> Option<UTF16Selection> {
        let selection = self.edit.selection();
        Some(UTF16Selection {
            range: to_utf16(self.text(), selection.start)..to_utf16(self.text(), selection.end),
            reversed: self.edit.cursor < self.edit.anchor,
        })
    }

    fn marked_text_range(&self, _: &mut Window, _: &mut Context<Self>) -> Option<Range<usize>> {
        self.edit
            .marked
            .as_ref()
            .map(|r| to_utf16(self.text(), r.start)..to_utf16(self.text(), r.end))
    }

    fn unmark_text(&mut self, _: &mut Window, cx: &mut Context<Self>) {
        self.edit.marked = None;
        cx.notify();
    }

    fn replace_text_in_range(
        &mut self,
        range: Option<Range<usize>>,
        text: &str,
        _: &mut Window,
        cx: &mut Context<Self>,
    ) {
        // Typed text: plain Enter arrives here as a line break, but Enter
        // belongs to the dialog; only Shift-Enter starts a new line.
        if matches!(text, "\n" | "\r" | "\r\n") {
            return;
        }
        self.edit.replace(range, text, false, None);
        self.did_edit(cx);
    }

    fn replace_and_mark_text_in_range(
        &mut self,
        range: Option<Range<usize>>,
        text: &str,
        selection: Option<Range<usize>>,
        _: &mut Window,
        cx: &mut Context<Self>,
    ) {
        self.edit.replace(range, text, true, selection);
        self.did_edit(cx);
    }

    fn bounds_for_range(
        &mut self,
        range: Range<usize>,
        _: Bounds<Pixels>,
        _: &mut Window,
        _: &mut Context<Self>,
    ) -> Option<Bounds<Pixels>> {
        let bounds = self.bounds?;
        let range = byte_range(self.text(), range);
        let start = self.position(range.start);
        let origin = point(
            bounds.left() + start.x,
            bounds.top() + start.y - self.scroll,
        );
        Some(Bounds::new(origin, size(px(1.), self.line_height())))
    }

    fn character_index_for_point(
        &mut self,
        position: Point<Pixels>,
        _: &mut Window,
        _: &mut Context<Self>,
    ) -> Option<usize> {
        let bounds = self.bounds?;
        if !bounds.contains(&position) {
            return None;
        }
        Some(to_utf16(self.text(), self.mouse_index(position)))
    }
}

impl Render for MultilineInput {
    fn render(&mut self, _: &mut Window, cx: &mut Context<Self>) -> impl IntoElement {
        let input = cx.entity();
        let painter = input.clone();
        let height = self.line_height();
        let visible = self.rows.clamp(MIN_ROWS, MAX_ROWS) as f32;
        div()
            .debug_selector(|| "multiline-input".into())
            .w_full()
            .px_2()
            .py_1()
            .rounded(px(crate::config::corners::CONTROL))
            .border_1()
            .border_color(rgb(self.theme.active))
            .bg(rgb(self.theme.background))
            .text_color(rgb(self.theme.foreground))
            .text_font(&self.font)
            .text_size(px(self.font.size))
            .line_height(height)
            .track_focus(&self.focus)
            .cursor(CursorStyle::IBeam)
            .on_key_down(cx.listener(Self::key_down))
            .on_action(cx.listener(|this, _: &actions::Cut, window, cx| {
                this.key_down(&actions::edit_key("x"), window, cx)
            }))
            .on_action(cx.listener(|this, _: &actions::Copy, window, cx| {
                this.key_down(&actions::edit_key("c"), window, cx)
            }))
            .on_action(cx.listener(|this, _: &actions::Paste, window, cx| {
                this.key_down(&actions::edit_key("v"), window, cx)
            }))
            .on_action(cx.listener(|this, _: &actions::SelectAll, window, cx| {
                this.key_down(&actions::edit_key("a"), window, cx)
            }))
            .on_mouse_down(
                MouseButton::Left,
                cx.listener(|this, event: &gpui::MouseDownEvent, window, cx| {
                    window.focus(&this.focus, cx);
                    cx.stop_propagation();
                    if !this.is_composing() {
                        let offset = this.mouse_index(event.position);
                        this.edit.select_to(offset, event.modifiers.shift);
                        this.selecting = true;
                    }
                    cx.notify();
                }),
            )
            .on_mouse_move(cx.listener(|this, event: &gpui::MouseMoveEvent, _, cx| {
                if this.selecting && !this.is_composing() {
                    this.edit.select_to(this.mouse_index(event.position), true);
                    cx.stop_propagation();
                    cx.notify();
                }
            }))
            .on_mouse_up(
                MouseButton::Left,
                cx.listener(|this, _, _, cx| {
                    if this.selecting {
                        this.selecting = false;
                        cx.stop_propagation();
                    }
                }),
            )
            .on_mouse_up_out(
                MouseButton::Left,
                cx.listener(|this, _, _, _| this.selecting = false),
            )
            .on_scroll_wheel(cx.listener(|this, event: &gpui::ScrollWheelEvent, _, cx| {
                let delta = event.delta.pixel_delta(this.line_height()).y;
                let max = (this.line_height() * this.rows as f32
                    - this.line_height() * this.rows.clamp(MIN_ROWS, MAX_ROWS) as f32)
                    .max(px(0.));
                this.scroll = (this.scroll - delta).clamp(px(0.), max);
                cx.notify();
            }))
            .child(
                canvas(
                    move |bounds, window, cx| {
                        let input = input.read(cx);
                        let placeholder = input.text().is_empty();
                        let text: gpui::SharedString = if placeholder {
                            input.placeholder.clone().into()
                        } else {
                            input.text().to_owned().into()
                        };
                        let run = TextRun {
                            len: text.len(),
                            font: window.text_style().font(),
                            color: rgb(if placeholder {
                                input.theme.muted
                            } else {
                                input.theme.foreground
                            })
                            .into(),
                            background_color: None,
                            underline: None,
                            strikethrough: None,
                        };
                        let runs: Vec<TextRun> = match &input.edit.marked {
                            Some(marked) if !placeholder => [
                                TextRun {
                                    len: marked.start,
                                    ..run.clone()
                                },
                                TextRun {
                                    len: marked.len(),
                                    underline: Some(UnderlineStyle {
                                        color: Some(run.color),
                                        thickness: px(1.),
                                        wavy: false,
                                    }),
                                    ..run.clone()
                                },
                                TextRun {
                                    len: text.len() - marked.end,
                                    ..run
                                },
                            ]
                            .into_iter()
                            .filter(|run| run.len > 0)
                            .collect(),
                            _ => vec![run],
                        };
                        let lines = window
                            .text_system()
                            .shape_text(
                                text.clone(),
                                px(input.font.size),
                                &runs,
                                Some(bounds.size.width.max(px(1.))),
                                None,
                            )
                            .unwrap_or_default();
                        // shape_text splits at `\n`; record where each paragraph starts.
                        let mut start = 0;
                        lines
                            .into_iter()
                            .map(|line| {
                                let entry = (start, line);
                                start += entry.1.len() + 1;
                                entry
                            })
                            .collect::<Vec<_>>()
                    },
                    move |bounds, lines, window, cx| {
                        painter.update(cx, |input, cx| {
                            let placeholder = input.text().is_empty();
                            let rows: usize = lines
                                .iter()
                                .map(|(_, line)| {
                                    (line.size(height).height / height).round() as usize
                                })
                                .sum::<usize>()
                                .max(1);
                            if rows != input.rows {
                                input.rows = rows;
                                cx.notify();
                            }
                            // A placeholder is painted but never edited: positions
                            // only count once there is text (see `position`).
                            input.layout = lines;
                            input.bounds = Some(bounds);
                            // Keep the caret in view.
                            let caret = input.position(input.edit.cursor);
                            let view = bounds.size.height;
                            input.scroll = input
                                .scroll
                                .min(caret.y)
                                .max(caret.y + height - view)
                                .max(px(0.));
                            window.handle_input(
                                &input.focus,
                                ViewInputHandler::new(bounds, cx.entity()),
                                cx,
                            );
                            let origin = point(bounds.left(), bounds.top() - input.scroll);
                            window.with_content_mask(Some(ContentMask { bounds }), |window| {
                                let focused = input.focus.is_focused(window);
                                let selection = input.edit.selection();
                                if focused && !selection.is_empty() && !placeholder {
                                    let from = input.position(selection.start);
                                    let to = input.position(selection.end);
                                    let mut y = from.y;
                                    while y <= to.y {
                                        let left = if y == from.y { from.x } else { px(0.) };
                                        let right =
                                            if y == to.y { to.x } else { bounds.size.width };
                                        window.paint_quad(fill(
                                            Bounds::from_corners(
                                                point(origin.x + left, origin.y + y),
                                                point(origin.x + right, origin.y + y + height),
                                            ),
                                            rgb(input.theme.active),
                                        ));
                                        y += height;
                                    }
                                }
                                let mut top = origin.y;
                                for (_, line) in &input.layout {
                                    let _ = line.paint(
                                        point(origin.x, top),
                                        height,
                                        TextAlign::Left,
                                        None,
                                        window,
                                        cx,
                                    );
                                    top += line.size(height).height;
                                }
                                if focused {
                                    window.paint_quad(fill(
                                        Bounds::new(
                                            point(origin.x + caret.x, origin.y + caret.y),
                                            size(px(1.), height),
                                        ),
                                        rgb(input.theme.cursor),
                                    ));
                                }
                            });
                        });
                    },
                )
                .w_full()
                .h(height * visible),
            )
    }
}

#[cfg(test)]
mod tests;
