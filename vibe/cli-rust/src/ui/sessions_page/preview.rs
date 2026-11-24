//! The highlighted session's preview: its title and state, its facts, then the
//! end of its conversation drawn like the conversation view, when it has room.

use super::super::theme;
use super::facts;
use super::legend::State;
use crate::app::App;
use crate::selection::Folds;
use crate::sessions_page::preview_view::View;
use crate::ui::transcript::{draw_tail, Shown, Tail};
use crate::utils::text::{capitalize, ellipsize, wrap_hard};
use ratatui::layout::Rect;
use ratatui::style::Modifier;
use ratatui::Frame;

/// Rows the title wraps to at most.
const TITLE_LINES: usize = 2;
/// Conversation rows worth drawing: with fewer, the preview shows no conversation.
const MIN_CONVERSATION: u16 = 4;

/// Paint the preview of the highlighted session into `area`; what a copy of
/// its conversation rejoins and skips comes back.
pub(super) fn draw(app: &mut App, f: &mut Frame, area: Rect) {
    let Some(session) = crate::sessions_page::selected(app).cloned() else {
        return;
    };
    let width = usize::from(area.width);
    let mut y = area.y;
    let title = theme::text(theme::text_primary()).add_modifier(Modifier::BOLD);
    let mut rows = wrap_hard(session.label(), width);
    if rows.len() > TITLE_LINES {
        rows.truncate(TITLE_LINES);
        if let Some(last) = rows.last_mut() {
            *last = ellipsize(&format!("{last}…"), width);
        }
    }
    for row in rows.iter().take(usize::from(area.height)) {
        f.buffer_mut().set_stringn(area.x, y, row, width, title);
        y += 1;
    }
    let current = app.session.session_id.as_deref() == Some(session.id.as_str());
    let state = State::of(app, &session, current);
    let mut line = format!("{} {}", state.glyph(), capitalize(state.label()));
    if current {
        line.push_str(" · current session");
    }
    if y < area.bottom() {
        let style = theme::text(state.color());
        f.buffer_mut().set_stringn(area.x, y, &line, width, style);
        y += 1;
    }
    // The facts one blank row under the state, as many as fit.
    y += 1;
    for (label, value) in facts::of(app, &session) {
        if y >= area.bottom() {
            break;
        }
        let spans = facts::row(label, &value, width);
        let mut x = area.x;
        for (text, style) in spans {
            let room = usize::from(area.right() - x);
            (x, _) = f.buffer_mut().set_stringn(x, y, &text, room, style);
        }
        y += 1;
    }
    // A rule one blank row under the facts, then the conversation one blank
    // row under it, only when enough of it shows.
    app.sessions_page.preview.view.area = Rect::default();
    app.sessions_page.preview.view.entries.clear();
    if area.bottom() < y + 3 + MIN_CONVERSATION {
        return;
    }
    let rule = "─".repeat(width);
    let style = theme::dim(theme::border_blurred());
    f.buffer_mut()
        .set_stringn(area.x, y + 1, &rule, width, style);
    // The conversation, then its scrollbar in the last column.
    let body = Rect::new(area.x, y + 3, area.width - 1, area.bottom() - y - 3);
    let collapse = app.view.collapse_turn_details;
    let sticky = app.view.sticky_prompt_enabled;
    let mouse = app.view.mouse_position;
    let show_reasoning = app.view.transcript.shows_reasoning();
    let preview = &mut app.sessions_page.preview;
    preview.touch(&session.id);
    if preview.painted.as_deref() != Some(session.id.as_str()) {
        preview.markdown = Default::default();
        preview.painted = Some(session.id.clone());
    }
    if preview.view.id.as_deref() != Some(session.id.as_str()) {
        preview.view = View {
            id: Some(session.id.clone()),
            ..View::default()
        };
    }
    // The wheel and clicks reach the scrollbar's column too.
    preview.view.area = Rect {
        width: body.width + 1,
        ..body
    };
    let markdown = &mut preview.markdown;
    let view = &mut preview.view;
    // A click folded an entry: the scroll follows the change, keeping it in place.
    let before = std::mem::take(&mut view.pin).then_some(view.total);
    let shown = Shown {
        expanded: &view.expanded,
        scroll: &mut view.scroll,
        before,
        collapse,
        // Only the attached session's live conversation can have a turn running.
        running: current && matches!(app.session.status, crate::app::Status::Generating { .. }),
        sticky,
        mouse,
    };
    // The current session previews its live conversation, as it is now.
    if current {
        let cache = &mut preview.current;
        let transcript = &app.view.transcript;
        let tail = draw_tail(f, body, transcript, cache, markdown, shown);
        publish(view, body, tail);
        return scrollbar(app, f, body);
    }
    let cached = preview
        .sessions
        .iter_mut()
        .find(|cached| cached.id == session.id);
    let Some((transcript, cache)) =
        cached.map(|cached| (&mut cached.transcript, &mut cached.cache))
    else {
        return note(f, body, "Loading conversation…");
    };
    let Some(transcript) = transcript else {
        return note(f, body, "Couldn't load the conversation");
    };
    transcript.set_show_reasoning(show_reasoning);
    let tail = draw_tail(f, body, transcript, cache, markdown, shown);
    publish(view, body, tail);
    scrollbar(app, f, body);
}

/// Keep what the painted conversation's clicks, scroll and selection need, until the next frame.
fn publish(view: &mut View, body: Rect, tail: Tail) {
    view.entries = tail.entries;
    view.total = tail.total;
    view.selection = crate::ui::selection::frame_region(body);
    view.row_chrome = tail.row_chrome;
    view.raws = tail.raws;
    view.hangs = tail.hangs;
    view.folds = Folds {
        x: body.x,
        rows: tail.folds,
    };
    view.margins = tail.margins;
    view.gaps = tail.pinned;
    view.links = tail.links;
}

/// The conversation's scrollbar right of `body`, when it does not all fit.
fn scrollbar(app: &mut App, f: &mut Frame, body: Rect) {
    let view = &app.sessions_page.preview.view;
    if view.total <= body.height {
        return;
    }
    let position = view.total - body.height - view.scroll.min(view.total - body.height);
    let bar = Rect::new(body.right(), body.y, 1, body.height);
    let (total, height) = (view.total, body.height);
    super::super::scrollbar::draw(
        app,
        f,
        crate::mouse::MouseTarget::SessionsPreview,
        bar,
        total,
        height,
        position,
    );
}

fn note(f: &mut Frame, area: Rect, text: &str) {
    let width = area.width.into();
    f.buffer_mut()
        .set_stringn(area.x, area.y, text, width, theme::muted_style());
}
