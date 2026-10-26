//! Bottom bar: working directory on the left, PR/MR link centered, active
//! model and thinking level + token budget on the right.
//! One artifact paints its link; several paint a count that opens the modal.

use ratatui::buffer::Buffer;
use ratatui::layout::Rect;
use ratatui::style::{Modifier, Style};
use ratatui::text::{Line, Span};
use ratatui::widgets::Paragraph;
use ratatui::Frame;

use super::theme;
use crate::app::App;
use crate::selection::{cells, Granularity};
use crate::utils::paths::collapse_home;
use crate::utils::text::{ellipsize, format_compact_count};
use unicode_width::UnicodeWidthStr;

/// Between the active model and the token counter.
const MODEL_SEP: &str = " · ";
/// Fewest cells an elided cwd keeps before the model is hidden to make room.
const CWD_FLOOR: usize = 16;
/// Fewest cells an elided model name keeps before it is hidden.
const MODEL_FLOOR: usize = 6;

/// What a click on the painted bar link does.
#[derive(Clone)]
pub enum BarLinkAction {
    /// Open this artifact's URL (the single-artifact case).
    OpenUrl(String),
    /// Open the artifacts modal (several artifacts).
    ListArtifacts,
}

/// The PR/MR link painted in the bar this frame, for mouse click routing.
pub struct BarLink {
    pub area: Rect,
    pub action: BarLinkAction,
}

pub fn draw(app: &mut App, f: &mut Frame, area: Rect) {
    crate::mouse::register_region(app, area, crate::mouse::MouseTarget::BottomBar);
    // Working directory from `App`; `-` if unresolved.
    let cwd = crate::replay::footer_cwd(
        app.session
            .cwd
            .as_deref()
            .map(collapse_home)
            .unwrap_or_else(|| "-".into()),
    );
    let muted = theme::muted_style();
    // Token budget, empty until the first `session/statsUpdated` stats arrive.
    let counter = format_context(app.session.tokens);
    // The banner's `Name[thinking]` label, so the footer shows the thinking level too.
    let model = app.session.startup_config.active_model.as_str();
    // While a quit is pending the path is replaced by the confirm hint.
    let quit = app.quit.active().map(|pending| {
        let extra = if pending.extra.is_empty() {
            String::new()
        } else {
            format!(" ({})", pending.extra)
        };
        Line::from(vec![
            Span::styled("Press ", muted),
            Span::styled(pending.key.label(), Style::default().fg(theme::primary())),
            Span::styled(format!(" again to quit{extra}"), muted),
        ])
    });
    // The quit hint, a one-cell spacer and the counter stay; the cwd and model share the rest.
    let fixed = quit.as_ref().map_or(0, Line::width);
    let cwd_cells = if quit.is_some() { 0 } else { cwd.width() };
    let room = (area.width as usize).saturating_sub(counter.width() + 1 + fixed);
    let separator = if counter.is_empty() {
        0
    } else {
        MODEL_SEP.width()
    };
    let (cwd_width, model_width) = split_room(room, cwd_cells, model.width(), separator);
    let model = ellipsize(model, model_width);
    let right = Line::from(Span::styled(right_text(&model, &counter), muted));
    let left = quit.unwrap_or_else(|| Line::from(Span::styled(ellipsize(&cwd, cwd_width), muted)));
    // `#spacer` `width: 1fr`: the right context sits at the edge.
    let left_width = (left.width() as u16).min(area.width);
    // The `1fr` spacer never collapses below one cell.
    let spaced = (area.x + left_width + 1).min(area.right());
    let right_x = area
        .right()
        .saturating_sub(right.width() as u16)
        .max(spaced);
    f.render_widget(
        Paragraph::new(left),
        Rect {
            width: left_width,
            ..area
        },
    );
    f.render_widget(
        Paragraph::new(right),
        Rect {
            x: right_x,
            width: area.right() - right_x,
            ..area
        },
    );
    // The model leads the right side; a click on it opens `/model`.
    app.view.footer_model_area = Rect {
        x: right_x,
        width: (model.width() as u16).min(area.right() - right_x),
        height: 1,
        ..area
    };
    if app.view.mouse_position.is_some_and(|at| model_at(app, at)) {
        f.buffer_mut()
            .set_style(app.view.footer_model_area, theme::link_hover_style());
    }
    draw_link(app, f, area, spaced, right_x);
    paint_selection(app, f, area);
}

/// Whether `at` is on the footer's model name while a click there may open `/model`, which needs an idle session like the typed command.
pub fn model_at(app: &App, at: (u16, u16)) -> bool {
    app.focus() == crate::focus::Focus::Composer
        && app.view.footer_model_area.contains(at.into())
        && crate::commands::submission::accepts_idle_command(app)
}

/// Paint the session's PR/MR affordance centered between the two edge
/// segments — the latest artifact's link when there is exactly one, else a
/// count that opens the artifacts modal — and publish its hit rect.
fn draw_link(app: &mut App, f: &mut Frame, area: Rect, lo: u16, hi: u16) {
    let link = match app.session.artifacts.len() {
        0 => None,
        1 => {
            let artifact = &app.session.artifacts[0];
            Some((
                // Phase 4: the link carries the synced status —
                // `PR #123 (mergeable)`, `MR !7 (merged)`.
                crate::artifacts::labeled(artifact),
                BarLinkAction::OpenUrl(artifact.external_url.clone()),
            ))
        }
        _ => Some((
            crate::artifacts::count_label(&app.session.artifacts),
            BarLinkAction::ListArtifacts,
        )),
    };
    let mut painted = None;
    if let Some((text, action)) = link {
        let width = text.width() as u16;
        if let Some(x) = center_x(area, width, lo, hi) {
            let rect = Rect { x, width, ..area };
            let style = theme::text(theme::md_link()).add_modifier(Modifier::UNDERLINED);
            f.render_widget(Paragraph::new(Span::styled(text, style)), rect);
            painted = Some(BarLink { area: rect, action });
        }
    }
    app.view.bottom_bar_link = painted;
}

/// Center a `width`-cell segment in `area`, clamped to `[lo, hi - width]` so it
/// never overlaps the edge segments; `None` when it does not fit.
pub fn center_x(area: Rect, width: u16, lo: u16, hi: u16) -> Option<u16> {
    let max_x = hi.checked_sub(width)?;
    if width == 0 || max_x < lo {
        return None;
    }
    let centered = area.x + (area.width - width) / 2;
    Some(centered.clamp(lo, max_x))
}

/// Highlight the bottom-bar selection and, on a pending release, copy its text.
fn paint_selection(app: &mut App, f: &mut Frame, area: Rect) {
    let Some((anchor, head)) = app
        .selection
        .bottom_bar
        .as_ref()
        .map(|sel| (sel.anchor, sel.head))
    else {
        return;
    };
    let lo = anchor.min(head).max(area.x);
    let hi = anchor.max(head).min(area.right().saturating_sub(1));
    if lo > hi {
        app.selection.bottom_bar = None;
        return;
    }
    // The chain picks char/word/paragraph; snap the span the same way the
    // transcript does, once the painted row is known.
    let (x0, x1) = selected_columns(f.buffer_mut(), area, lo, hi, app.selection.granularity);
    // Cache the text every paint so a copy key can read it with autocopy off.
    let text = read_row(f.buffer_mut(), area.y, x0, x1);
    if let Some(sel) = app.selection.bottom_bar.as_mut() {
        sel.text = text;
    }
    honor_pending_copy(app);
    let style = Style::default()
        .fg(theme::selection_fg())
        .bg(theme::selection_bg());
    let buf = f.buffer_mut();
    for x in x0..=x1 {
        if let Some(cell) = buf.cell_mut((x, area.y)) {
            cell.set_style(cell.style().patch(style));
        }
    }
}

/// Copy a bottom-bar selection whose release armed a deferred copy, using the
/// text cached at paint time so no frame buffer is read. Called from paint and
/// before a new press supersedes the selection, so the copy is never dropped.
pub fn honor_pending_copy(app: &mut App) {
    let Some(sel) = app.selection.bottom_bar.as_ref() else {
        return;
    };
    if !sel.pending_copy {
        return;
    }
    let text = sel.text.clone();
    crate::ui::notice::copy_if_autocopy(app, &text);
    if let Some(sel) = app.selection.bottom_bar.as_mut() {
        sel.pending_copy = false;
    }
}

/// Widen the raw column range `[lo, hi]` to the click granularity, reading the
/// painted row: a word snaps to its `\w+` run, a paragraph to the whole line.
pub fn selected_columns(
    buf: &Buffer,
    area: Rect,
    lo: u16,
    hi: u16,
    granularity: Granularity,
) -> (u16, u16) {
    let y = area.y;
    let right = area.right().saturating_sub(1);
    match granularity {
        Granularity::Char => (lo, hi),
        Granularity::Word => (
            cells::word_start(buf, lo, area.x, y),
            cells::word_end(buf, hi, right, y),
        ),
        Granularity::Paragraph => {
            let x0 = cells::skip_blanks_right(buf, area.x, right, y);
            (x0, cells::trim_blanks_left(buf, right, x0, y))
        }
    }
}

/// Read cells `[x0, x1]` on screen row `y` back as text, trimming trailing blanks.
fn read_row(buf: &Buffer, y: u16, x0: u16, x1: u16) -> String {
    let row: String = (x0..=x1)
        .filter_map(|x| buf.cell((x, y)).map(|cell| cell.symbol()))
        .collect();
    row.trim_end().to_string()
}

/// `(cwd, model)` cells in `room`, with `separator` cells before a shown model: the model elides to its floor, the cwd to its, then the model hides and the cwd takes the rest.
pub fn split_room(room: usize, cwd: usize, model: usize, separator: usize) -> (usize, usize) {
    let shown = room.saturating_sub(separator);
    let model_floor = model.min(MODEL_FLOOR);
    if model == 0 || shown < cwd.min(CWD_FLOOR) + model_floor {
        return (cwd.min(room), 0);
    }
    let cwd = cwd.min(shown - model_floor);
    (cwd, model.min(shown - cwd))
}

/// The right side: the (elided) model, then the counter, either one possibly empty.
fn right_text(model: &str, counter: &str) -> String {
    match (model.is_empty(), counter.is_empty()) {
        (true, _) => counter.to_owned(),
        (false, true) => model.to_owned(),
        (false, false) => format!("{model}{MODEL_SEP}{counter}"),
    }
}

/// Render the context budget `(current, max)`; empty while `max == 0`.
fn format_context((current, max): (u64, u64)) -> String {
    if max == 0 {
        return String::new();
    }
    let ratio = (current as f64 / max as f64).min(1.0);
    let pct = (ratio * 100.0).round() as u64;
    format!(
        "{}/{} tokens ({pct}%)",
        format_compact_count(current),
        format_compact_count(max),
    )
}
