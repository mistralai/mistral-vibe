//! Bottom bar: cwd (with a copy icon once shortened) and PR/MR link on the left; usage and context gauges on the right.
//! The PR/MR link (one active artifact, or a count) opens the artifacts modal.

use ratatui::buffer::Buffer;
use ratatui::layout::Rect;
use ratatui::style::Style;
use ratatui::text::{Line, Span};
use ratatui::widgets::Paragraph;
use ratatui::Frame;

use super::bottom_bar_parts::{link_input, spans};
use super::footer_fit::{self, Tone, COPY_ICON, GAP, GAUGE_SEP};
use super::theme;
use crate::app::App;
use crate::selection::{cells, Granularity};
use crate::utils::paths::collapse_home;
use unicode_width::UnicodeWidthStr;

/// What a click on a painted bar link does.
#[derive(Clone)]
pub enum BarLinkAction {
    /// Open the artifacts modal.
    ListArtifacts,
    /// Copy the whole working directory.
    CopyPath(String),
}

/// A link painted in the bar this frame, for mouse click routing.
pub struct BarLink {
    pub area: Rect,
    pub action: BarLinkAction,
}

pub fn draw(app: &mut App, f: &mut Frame, area: Rect) {
    crate::mouse::register_region(app, area, crate::mouse::MouseTarget::BottomBar);
    let cwd = crate::replay::footer_cwd(
        app.session
            .cwd
            .as_deref()
            .map(collapse_home)
            .unwrap_or_else(|| "-".into()),
    );
    let muted = theme::muted_style();
    // While a quit is pending the left side is replaced by the confirm hint.
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
    // The history search line takes the quit hint's slot.
    let quit = quit.or_else(|| super::reverse_search::line(app));
    let link = link_input(app);
    let path = app.session.cwd.clone().map(crate::replay::footer_cwd);
    let fitted = footer_fit::fit(footer_fit::Inputs {
        width: area.width as usize,
        cwd: &cwd,
        copy_icon: path.is_some(),
        quit: quit.as_ref().map(Line::width),
        link,
        tokens: app.session.tokens,
        usage: crate::usage_gauge::footer_usage(app),
    });
    let mut left = quit.map_or_else(Vec::new, |line| line.spans);
    // Each link's span range in `left` and its click action.
    let mut links = Vec::new();
    if !fitted.cwd.is_empty() {
        left.push(Span::styled(fitted.cwd, muted));
    }
    if let Some(path) = path.filter(|_| fitted.copy_icon) {
        left.push(Span::raw(" "));
        let start = left.len();
        left.extend(spans(vec![(COPY_ICON.to_owned(), Tone::Text)]));
        links.push((start, left.len(), BarLinkAction::CopyPath(path)));
    }
    if !fitted.link.is_empty() {
        if !left.is_empty() {
            left.push(Span::raw(GAP));
        }
        let start = left.len();
        left.extend(spans(fitted.link));
        links.push((start, left.len(), BarLinkAction::ListArtifacts));
    }
    let mut right = Vec::new();
    let context_width = fitted
        .context
        .iter()
        .map(|(text, _)| text.width())
        .sum::<usize>() as u16;
    for gauge in [fitted.usage, fitted.context] {
        if gauge.is_empty() {
            continue;
        }
        if !right.is_empty() {
            right.push(Span::styled(GAUGE_SEP, muted));
        }
        right.extend(spans(gauge));
    }
    let left = Line::from(left);
    let right_width = right.iter().map(Span::width).sum::<usize>() as u16;
    let right = Line::from(right);
    let left_width = (left.width() as u16).min(area.width);
    // The spacer never collapses below one cell.
    let spaced = (area.x + left_width + 1).min(area.right());
    let right_x = area.right().saturating_sub(right_width).max(spaced);
    app.view.bottom_bar_links = links
        .into_iter()
        .map(|(from, to, action)| {
            let x = area.x + left.spans[..from].iter().map(Span::width).sum::<usize>() as u16;
            let width = left.spans[from..to].iter().map(Span::width).sum::<usize>() as u16;
            BarLink {
                area: Rect {
                    x,
                    width: width.min(area.right().saturating_sub(x)),
                    height: 1,
                    ..area
                },
                action,
            }
        })
        .collect();
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
    // The context gauge ends the right side; a click on it opens `/context`.
    app.view.footer_tokens_area = if context_width == 0 {
        Rect::default()
    } else {
        Rect {
            x: right_x + right_width.saturating_sub(context_width),
            width: context_width.min(area.right() - right_x),
            height: 1,
            ..area
        }
    };
    if app.view.mouse_position.is_some_and(|at| tokens_at(app, at)) {
        super::list_hover::paint_button(f.buffer_mut(), app.view.footer_tokens_area);
    }
    // The copy icon keeps its look under the pointer; only the hand says it is clickable.
    let hovered = app.view.mouse_position.and_then(|at| link_at(app, at));
    if let Some(link) = hovered.filter(|link| !matches!(link.action, BarLinkAction::CopyPath(_))) {
        super::list_hover::paint_button(f.buffer_mut(), link.area);
    }
    super::reverse_search::draw_caret(app, f, area);
    paint_selection(app, f, area);
}

/// Whether `at` is on the footer's context gauge; a click there toggles `/context`, a side-channel read that also runs while a turn generates.
pub fn tokens_at(app: &App, at: (u16, u16)) -> bool {
    use crate::focus::Focus;
    matches!(app.focus(), Focus::Composer | Focus::ContextApp)
        && !app.view.footer_tokens_area.is_empty()
        && app.view.footer_tokens_area.contains(at.into())
}

/// Whether a click at `at` acts on the footer (the context gauge or a painted link) instead of selecting text.
pub fn clickable_at(app: &App, at: (u16, u16)) -> bool {
    tokens_at(app, at) || link_at(app, at).is_some()
}

/// The painted link at `at` (the cwd's copy icon or the PR/MR link), unless an overlay drawn over the footer blocks it.
pub fn link_at(app: &App, at: (u16, u16)) -> Option<&BarLink> {
    use crate::focus::Focus;
    let focus = app.focus();
    if matches!(focus, Focus::Config | Focus::ReleaseNotes) {
        return None;
    }
    // The PR list would open under any panel that has focus over it.
    let lists = matches!(
        focus,
        Focus::ArtifactsModal | Focus::SubagentList | Focus::Composer
    );
    app.view
        .bottom_bar_links
        .iter()
        .filter(|link| lists || !matches!(link.action, BarLinkAction::ListArtifacts))
        .find(|link| link.area.contains(at.into()))
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
