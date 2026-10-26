//! The artifacts modal bottom-app: a bordered box with a title, one row per
//! PR/MR artifact (link label plus dim title), the highlighted row's URL, and
//! a hint.

use ratatui::layout::Rect;
use ratatui::style::{Modifier, Style};
use ratatui::widgets::{Block, Borders, Clear};
use ratatui::Frame;
use unicode_width::UnicodeWidthStr;

use super::{hint_line, list_cursor, scrollbar, theme};
use crate::app::App;
use crate::artifacts;
use crate::artifacts_modal::{option_count, selected};
use crate::hints::{self, action, key, Hint};
use crate::utils::text::ellipsize;

/// The modal's footer hints: navigate, open the highlighted link, cancel.
const HINTS: &[Hint] = &[hints::NAVIGATE, (key::ENTER, action::OPEN), hints::CANCEL];

/// Draw the whole screen with the modal replacing the input box; the chat
/// shrinks to make room for the bottom-app.
pub fn draw(app: &mut App, f: &mut Frame, area: Rect) {
    let height = box_height(app, area.height);
    let kind = super::bottom_app::Kind::Artifacts;
    super::bottom_app::draw(app, f, area, height, kind, draw_box);
}

/// Visible option rows: `min(count, 50vh)`.
fn visible_rows(app: &App, area_height: u16) -> usize {
    (area_height as usize / 2).min(option_count(app))
}

/// At most this many check lines are shown for the highlighted artifact;
/// the rest collapse into a `+N more` line.
const MAX_CHECK_LINES: usize = 3;

/// The highlighted artifact's CI checks (Phase 4), one dim line per check:
/// `+ name` passed, `x name` failed, `o name` pending.
fn checks_lines(app: &App) -> Vec<String> {
    let Some(artifact) = selected(app) else {
        return Vec::new();
    };
    let Some(checks) = artifact
        .details
        .as_ref()
        .and_then(|details| details.checks.as_ref())
    else {
        return Vec::new();
    };
    let mut lines: Vec<String> = checks
        .checks
        .iter()
        .filter_map(|check| {
            let name = check.name.as_deref()?;
            let mark = match check.status.as_deref() {
                Some("pass") => "+",
                Some("fail") => "x",
                _ => "o",
            };
            Some(format!("{mark} {name}"))
        })
        .collect();
    if lines.len() > MAX_CHECK_LINES {
        let extra = lines.len() - MAX_CHECK_LINES;
        lines.truncate(MAX_CHECK_LINES);
        lines.push(format!("+{extra} more"));
    }
    lines
}

/// Total box height: 2 borders + title + options + URL + hint, plus the
/// highlighted artifact's check lines.
fn box_height(app: &App, area_height: u16) -> u16 {
    visible_rows(app, area_height) as u16 + 5 + checks_lines(app).len() as u16
}

fn draw_box(app: &mut App, f: &mut Frame, area: Rect) {
    if area.height < 5 {
        return;
    }
    f.render_widget(Clear, area);
    f.buffer_mut()
        .set_style(area, Style::default().bg(theme::background()));
    let border = Style::default()
        .fg(theme::popup_border())
        .bg(theme::background());
    f.render_widget(
        Block::default().borders(Borders::ALL).border_style(border),
        area,
    );

    let bx = area.x;
    let by = area.y;
    let w = area.width;
    let h = area.height;

    f.buffer_mut().set_string(
        bx + 2,
        by + 1,
        artifacts::modal_title(&app.session.artifacts),
        Style::default()
            .fg(theme::primary())
            .bg(theme::background())
            .add_modifier(Modifier::BOLD),
    );

    let visible = h.saturating_sub(5) as usize;
    let top = by + 2;
    let total = option_count(app);
    let list_area = Rect::new(bx + 1, top, w.saturating_sub(2), visible as u16);
    // Without overflow there is no scrollbar gutter, so the options span one
    // column further right (Textual `OptionList` option width).
    let has_scrollbar = total > visible;
    let gutter = if has_scrollbar { 7 } else { 6 };
    let options = Rect::new(bx + 3, top, w.saturating_sub(gutter), visible as u16);
    let offset = app.artifacts_modal.list.layout_rows(total, options);
    crate::mouse::register_region(app, list_area, crate::mouse::MouseTarget::ArtifactsModal);

    for (row, i) in (offset..total).take(visible).enumerate() {
        let y = top + row as u16;
        draw_option(
            app,
            f,
            Rect {
                y,
                height: 1,
                ..options
            },
            i,
        );
    }

    if has_scrollbar {
        let bar = Rect::new(bx + w - 4, top, 1, visible as u16);
        scrollbar::draw(
            app,
            f,
            crate::mouse::MouseTarget::ArtifactsModal,
            bar,
            total as u16,
            visible as u16,
            offset as u16,
        );
    }

    // The highlighted artifact's CI checks (Phase 4), dim, between the rows
    // and the URL line; drawn bottom-up so the block hugs the URL line.
    let checks = checks_lines(app);
    for (index, line) in checks.iter().enumerate().rev() {
        let y = by + h - 4 - index as u16;
        f.buffer_mut().set_string(
            bx + 2,
            y,
            ellipsize(line, w.saturating_sub(4) as usize),
            theme::muted_style(),
        );
    }

    // The highlighted artifact's link, dim, between the rows and the hint.
    let url = selected(app).map(|artifact| artifact.external_url.clone());
    if let Some(url) = url {
        let width = w.saturating_sub(4) as usize;
        f.buffer_mut().set_string(
            bx + 2,
            by + h - 3,
            ellipsize(&url, width),
            theme::muted_style(),
        );
    }

    hint_line::draw(f, bx + 2, by + h - 2, HINTS);
}

/// One option row: the blank marker, the link label, and the dim title.
fn draw_option(app: &App, f: &mut Frame, row: Rect, i: usize) {
    let is_hl = i == app.artifacts_modal.list.selected();
    let (x, y) = (row.x, row.y);
    if is_hl {
        list_cursor::paint(f, row);
    }
    let (base, dim) = list_cursor::styles(is_hl);
    let artifact = &app.session.artifacts[i];
    f.buffer_mut().set_string(x, y, list_cursor::BLANK, base);
    // Phase 4: the row label carries the synced status, same as the bar link.
    let label = artifacts::labeled(artifact);
    // A highlighted row is bold across the whole option (block cursor); the
    // link style marks the label on the plain rows.
    let label_style = if is_hl {
        base.add_modifier(Modifier::BOLD)
    } else {
        theme::text(theme::md_link()).add_modifier(Modifier::UNDERLINED)
    };
    f.buffer_mut().set_string(x + 2, y, &label, label_style);
    if let Some(title) = artifact.display_title.as_deref() {
        let title_x = x + 2 + label.width() as u16 + 2;
        let width = row.right().saturating_sub(title_x) as usize;
        f.buffer_mut()
            .set_string(title_x, y, ellipsize(title, width), dim);
    }
}
