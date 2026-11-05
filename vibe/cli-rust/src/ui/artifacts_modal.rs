//! The artifacts modal: one row per PR/MR, the highlighted one's CI checks and URL, and a hint.

use ratatui::layout::Rect;
use ratatui::style::{Color, Modifier, Style};
use ratatui::widgets::{Block, Borders, Clear};
use ratatui::Frame;
use unicode_width::UnicodeWidthStr;

use super::artifacts_checks::{self, CheckLine};
use super::{hint_line, list_cursor, scrollbar, theme};
use crate::app::App;
use crate::artifacts::{self, Standing};
use crate::artifacts_modal::{option_count, selected};
use crate::hints::{self, action, key, Hint};
use crate::utils::text::ellipsize;

/// The modal's footer hints: navigate, open the highlighted link, cancel with Esc or Ctrl+C.
const HINTS: &[Hint] = &[
    hints::NAVIGATE,
    (key::ENTER, action::OPEN),
    (key::ESC_CTRL_C, action::CANCEL),
];

/// Draw the whole screen with the modal replacing the input box; the chat
/// shrinks to make room for the bottom-app.
pub fn draw(app: &mut App, f: &mut Frame, area: Rect) {
    let budget = visible_rows(app, area.height);
    let height = box_height(app, area.height);
    let kind = super::bottom_app::Kind::Artifacts;
    super::bottom_app::draw(app, f, area, height, kind, |app, f, area| {
        draw_box(app, f, area, budget)
    });
}

/// Borders, title, URL and hint around the rows.
const CHROME: u16 = 5;

/// Visible option rows: `min(count, 50vh)`.
fn visible_rows(app: &App, area_height: u16) -> usize {
    (area_height as usize / 2).min(option_count(app))
}

/// Rows kept for checks: the most any artifact shows, so moving the highlight never resizes the box.
fn checks_rows(app: &App) -> u16 {
    let most = app
        .session
        .artifacts
        .iter()
        .map(|artifact| artifacts_checks::lines(Some(artifact)).len())
        .max()
        .unwrap_or(0);
    most as u16
}

/// Total box height: the rows, the checks block and the chrome.
fn box_height(app: &App, area_height: u16) -> u16 {
    visible_rows(app, area_height) as u16 + checks_rows(app) + CHROME
}

/// `budget` caps the list rows like `box_height` did, so an overflowing list leaves the checks their rows.
fn draw_box(app: &mut App, f: &mut Frame, area: Rect, budget: usize) {
    if area.height < CHROME + 1 {
        return;
    }
    f.render_widget(Clear, area);
    f.buffer_mut()
        .set_style(area, Style::default().bg(theme::background()));
    let border = theme::border_style().bg(theme::background());
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

    // A short box keeps one row per option first; checks only take what is left.
    let total = option_count(app);
    let rows = h.saturating_sub(CHROME);
    let visible = usize::from(rows).min(budget.max(1));
    let checks_height = checks_rows(app).min(rows - visible as u16);
    let top = by + 2;
    let list_area = Rect::new(bx + 1, top, w.saturating_sub(2), visible as u16);
    // Without overflow there is no scrollbar gutter, so the options span one
    // column further right (Textual `OptionList` option width).
    let has_scrollbar = total > visible;
    let gutter = if has_scrollbar { 7 } else { 6 };
    let options = Rect::new(bx + 3, top, w.saturating_sub(gutter), visible as u16);
    app.artifacts_modal.list_area = options;
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

    // The highlighted artifact's CI checks in their own rows under the list: a colored mark, a dim name.
    let checks_top = top + visible as u16;
    let width = w.saturating_sub(6) as usize;
    for (row, line) in artifacts_checks::lines(selected(app))
        .iter()
        .take(checks_height as usize)
        .enumerate()
    {
        let y = checks_top + row as u16;
        let text = match line {
            CheckLine::Check(outcome, name) => {
                let (mark, style) = outcome.mark();
                f.buffer_mut().set_string(bx + 2, y, mark, style);
                name
            }
            CheckLine::More(text) => text,
        };
        f.buffer_mut()
            .set_string(bx + 4, y, ellipsize(text, width), theme::muted_style());
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

/// One option row: the state dot, the label with its status, and the dim title.
fn draw_option(app: &App, f: &mut Frame, row: Rect, i: usize) {
    let is_hl = i == app.artifacts_modal.list.selected();
    let (x, y) = (row.x, row.y);
    if is_hl {
        list_cursor::paint(f, row);
    }
    let (base, dim) = list_cursor::styles(is_hl);
    let artifact = &app.session.artifacts[i];
    let dot = list_cursor::accent(base, standing_color(artifacts::standing(artifact)));
    f.buffer_mut().set_string(x, y, STATE_DOT, dot);
    let mut next_x = x + STATE_DOT.width() as u16;
    let status = artifacts::status_label(artifact).map(|status| format!(" {status}"));
    let title = artifact
        .display_title
        .as_deref()
        .map(|title| format!("  {title}"));
    // Every part is cut at the row's right edge, so a narrow box never paints over its border.
    for (text, style) in [
        (Some(artifacts::label(artifact)), base),
        (status, dim),
        (title, dim),
    ] {
        let Some(text) = text else { continue };
        let room = row.right().saturating_sub(next_x) as usize;
        let text = ellipsize(&text, room);
        f.buffer_mut().set_string(next_x, y, &text, style);
        next_x += text.width() as u16;
    }
}

/// Leads each row: its color gives the PR/MR state.
const STATE_DOT: &str = "● ";

/// The state dot's colors, like GitHub's.
fn standing_color(standing: Standing) -> Color {
    match standing {
        Standing::Ready => theme::text_success(),
        Standing::Blocked => theme::text_error(),
        Standing::Merged => theme::text_accent(),
        Standing::Pending => theme::muted(),
    }
}
