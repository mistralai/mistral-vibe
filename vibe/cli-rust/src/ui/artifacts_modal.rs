//! The PR window's title, hints and rows (state dot, number, title, colored state), framed by the rail window.

use ratatui::layout::Rect;
use ratatui::style::Modifier;
use ratatui::text::{Line, Span};
use ratatui::Frame;

use super::pr_row::{self, Columns};
use super::{list_cursor, scrollbar, theme};
use crate::app::App;
use crate::artifacts;
use crate::artifacts_modal::{option_count, selected};
use crate::hints::{self, action, key, Hint};

/// `Pull Requests 2`: the providers present, then the count.
pub fn title(app: &App) -> Vec<Span<'static>> {
    let bold = theme::text(theme::foreground()).add_modifier(Modifier::BOLD);
    vec![
        Span::styled(artifacts::modal_title(&app.session.artifacts), bold),
        Span::styled(format!(" {}", option_count(app)), theme::muted_style()),
    ]
}

/// Navigate, open the highlighted link, its check and fix keys when they apply, close.
pub fn hints(app: &App) -> Vec<Hint> {
    let selected = selected(app);
    let mut hints = vec![hints::NAVIGATE, (key::ENTER, action::OPEN)];
    if selected
        .and_then(crate::artifacts_modal::check_url)
        .is_some()
    {
        hints.push(("c", action::OPEN_CHECK));
    }
    if selected
        .and_then(crate::artifacts_modal::fix_prompt)
        .is_some()
    {
        hints.push(("f", action::FIX));
    }
    hints.push(hints::CLOSE);
    hints
}

/// One row per artifact in `content`, the highlighted one on the list-cursor bar, a scrollbar on overflow.
pub fn draw_rows(app: &mut App, f: &mut Frame, content: Rect) {
    let total = option_count(app);
    let scrolls = total > usize::from(content.height);
    let options = Rect {
        width: content.width.saturating_sub(if scrolls { 2 } else { 0 }),
        ..content
    };
    app.artifacts_modal.list_area = options;
    let offset = app.artifacts_modal.list.layout_rows(total, options);
    let target = crate::mouse::MouseTarget::ArtifactsModal;
    crate::mouse::register_region(app, content, target);
    let prs: Vec<_> = app.session.artifacts.iter().collect();
    let columns = Columns::fit(&prs, options.width.into());
    let selected = app.artifacts_modal.list.selected();
    for (row, i) in (offset..total).take(options.height.into()).enumerate() {
        let row = Rect {
            y: options.y + row as u16,
            height: 1,
            ..options
        };
        if i == selected {
            list_cursor::paint(f, row);
        }
        let line = Line::from(pr_row::spans(prs[i], columns, i == selected));
        f.buffer_mut().set_line(row.x, row.y, &line, row.width);
    }
    if scrolls {
        let bar = Rect {
            x: content.right() - 1,
            width: 1,
            ..content
        };
        let (total, visible) = (total as u16, options.height);
        scrollbar::draw(app, f, target, bar, total, visible, offset as u16);
    }
}
