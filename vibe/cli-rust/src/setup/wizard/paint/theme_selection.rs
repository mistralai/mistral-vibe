//! The onboarding theme-selection screen (Python `ThemeSelectionScreen`).

use ratatui::layout::{Alignment, Constraint, Layout, Rect};
use ratatui::style::{Modifier, Style};
use ratatui::widgets::{Block, BorderType, Borders, Paragraph};
use ratatui::Frame;

use crate::app::App;
use crate::setup::wizard::OnboardingState;
use crate::ui::{scrollbar, theme};

const PREVIEW_MD: &str = r#"### Heading

**Bold**, *italic*, and `inline code`.

- Bullet point
- Another bullet point

1. First item
2. Second item

```python
def greet(name: str = "World") -> str:
    return f"Hello, {name}!"
```

> Blockquote

---

| Column 1 | Column 2 |
|----------|----------|
| Item 1   | Item 2   |"#;

/// $text — the hint and selected-item text color (`auto 87%` on truecolor themes).
fn text_color() -> ratatui::style::Color {
    if theme::is_ansi() {
        theme::foreground()
    } else {
        theme::blend(theme::background(), theme::auto_contrast(), 0.87)
    }
}

pub(super) fn draw(app: &mut App, wizard: &mut OnboardingState, f: &mut Frame, area: Rect) {
    // The fixed stack (title, list, gaps) needs 14 rows; below that nothing
    // is drawn rather than written past the buffer bottom.
    if area.height < 14 {
        return;
    }
    // Paint-time hit targets are rebuilt every frame.
    wizard.theme_rows.clear();
    let opts = crate::theme_picker::options();
    let total = opts.len();
    let selected = wizard.theme_index.min(total - 1);
    let half = 3;
    let preview = preview_pane(area.width, area.height.saturating_sub(17).max(7));
    // The preview may not push the stack past the screen: the fixed rows
    // (title, list, gaps) need 14, and a rect outside the buffer panics.
    let preview_h = preview.0.min(area.height.saturating_sub(14));
    // `#theme-outer { align: center middle }`: the title, list, and pane stack
    // centers vertically, leaving (screen - stack) / 2 rows of slack above.
    let top = area.height.saturating_sub(14 + preview_h) / 2;
    let chunks = Layout::vertical([
        Constraint::Length(top),
        Constraint::Length(1),
        Constraint::Length(2),
        Constraint::Length((half * 2 + 3) as u16),
        Constraint::Length(2),
        Constraint::Length(preview_h),
    ])
    .split(area);
    let title = "Select your preferred theme";
    f.buffer_mut().set_string(
        area.x + area.width.saturating_sub(title.len() as u16) / 2,
        chunks[1].y,
        title,
        Style::default().fg(theme::foreground()),
    );
    let list_area = chunks[3];
    let list_w = 30u16;
    let list_x = list_area.x + (list_area.width.saturating_sub(list_w)) / 2;
    let selected_y = list_area.y + half as u16;
    f.render_widget(
        Block::default()
            .borders(Borders::ALL)
            .border_type(BorderType::Rounded)
            .border_style(Style::default().fg(theme::popup_border())),
        Rect::new(list_x, selected_y, list_w, 3),
    );
    let centered = format!(
        "{:^width$}",
        opts[selected],
        width = (list_w as usize).saturating_sub(2)
    );
    // Python's theme screen has no hover affordance; the selection renders
    // identically whether or not the pointer sits on it.
    let selected_style = Style::default()
        .fg(text_color())
        .add_modifier(Modifier::BOLD);
    f.buffer_mut()
        .set_string(list_x + 1, selected_y + 1, &centered, selected_style);
    wizard.theme_rows.push((
        Rect::new(list_x + 1, selected_y + 1, list_w - 2, 1),
        selected,
    ));
    draw_navigation_hints(f, list_x, list_w, selected_y + 1);
    draw_neighboring_themes(app, wizard, f, list_area, list_x, list_w, selected, &opts);
    // The whole list wheel-navigates and click-selects through one region.
    crate::mouse::register_region(
        app,
        Rect::new(list_x, list_area.y, list_w, list_area.height),
        crate::mouse::MouseTarget::OnboardingThemeList,
    );
    draw_preview(app, wizard, f, chunks[5], preview);
}

/// Whether the pointer currently sits inside `rect` (hover highlight).
fn hovered_row(app: &App, rect: Rect) -> bool {
    app.view
        .mouse_position
        .is_some_and(|(col, row)| rect.contains((col, row).into()))
}

fn draw_navigation_hints(f: &mut Frame, list_x: u16, list_w: u16, y: u16) {
    let text = Style::default().fg(text_color());
    let key = Style::default()
        .fg(theme::primary())
        .add_modifier(Modifier::BOLD);
    f.buffer_mut()
        .set_string(list_x.saturating_sub(13), y, "Navigate ", text);
    f.buffer_mut()
        .set_string(list_x.saturating_sub(4), y, "↑↓", key);
    f.buffer_mut()
        .set_string(list_x + list_w + 2, y, "Press ", text);
    f.buffer_mut()
        .set_string(list_x + list_w + 8, y, "Enter", key);
    f.buffer_mut()
        .set_string(list_x + list_w + 13, y, " ↵", text);
}

#[allow(clippy::too_many_arguments)]
fn draw_neighboring_themes(
    app: &mut App,
    wizard: &mut OnboardingState,
    f: &mut Frame,
    area: Rect,
    list_x: u16,
    list_w: u16,
    selected: usize,
    options: &[&str],
) {
    let half = 3;
    for i in 0..half {
        for offset in [-(i as isize + 1), i as isize + 1] {
            let index = ((selected as isize + offset).rem_euclid(options.len() as isize)) as usize;
            let y = if offset < 0 {
                area.y + (offset + half as isize) as u16
            } else {
                area.y + half as u16 + 2 + offset as u16
            };
            let centered = format!("{:^width$}", options[index], width = list_w as usize);
            let row = Rect::new(list_x, y, list_w, 1);
            let hovered = hovered_row(app, row);
            let style = if hovered {
                // Hover brightens a faded neighbor to full foreground.
                Style::default()
                    .fg(theme::foreground())
                    .add_modifier(Modifier::BOLD)
            } else {
                fade_style(offset.unsigned_abs().min(3))
            };
            f.buffer_mut().set_string(list_x, y, &centered, style);
            wizard.theme_rows.push((row, index));
        }
    }
}

/// Fade-1/2/3 — text-opacity 50%/25%/10% blended over the background.
fn fade_style(distance: usize) -> Style {
    if theme::is_ansi() {
        return theme::dim(theme::foreground());
    }
    const FACTORS: [f32; 3] = [0.50, 0.25, 0.10];
    let factor = FACTORS[distance.saturating_sub(1).min(2)];
    Style::default().fg(theme::blend(
        theme::background(),
        theme::foreground(),
        factor,
    ))
}

/// The pane's geometry and content: the rendered Markdown, the pane height
/// (content height, clipped by the Python `max-height`), and the content width
/// (the container scrollbar, when the pane clips, narrows the widget by two
/// columns).
type Preview = (u16, u16, Vec<ratatui::text::Line<'static>>);

fn preview_pane(area_width: u16, max_height: u16) -> Preview {
    let width = 70u16.min(area_width);
    let full = width.saturating_sub(4);
    let lines = crate::ui::markdown::render_widget(PREVIEW_MD, full);
    let pane_h = lines.len().min(u16::MAX as usize) as u16 + 2;
    if pane_h <= max_height {
        return (pane_h, full, lines);
    }
    let content_w = full.saturating_sub(2);
    (
        max_height,
        content_w,
        crate::ui::markdown::render_widget(PREVIEW_MD, content_w),
    )
}

/// The preview pane (Python `#preview`): a bordered, scrollable container
/// holding a plain `Markdown` widget, so it renders with Textual's own
/// defaults. The scrollbar is the shared, draggable one.
fn draw_preview(
    app: &mut App,
    wizard: &mut OnboardingState,
    f: &mut Frame,
    area: Rect,
    preview: Preview,
) {
    let (pane_h, content_w, lines) = preview;
    // The chunk shrinks with the screen; a pane taller than it would hand
    // the widgets a rect outside the buffer.
    let pane_h = pane_h.min(area.height);
    let width = 70u16.min(area.width);
    let x = area.x + (area.width.saturating_sub(width)) / 2;
    f.render_widget(
        Block::default()
            .borders(Borders::ALL)
            .border_type(BorderType::Rounded)
            .border_style(Style::default().fg(theme::popup_border()))
            .title(" Preview ")
            .title_alignment(Alignment::Center),
        Rect::new(x, area.y, width, pane_h),
    );
    let window = pane_h.saturating_sub(2);
    let doc = lines.len().min(u16::MAX as usize) as u16;
    // A drag writes its live position through the shared scrollbar capture
    // (the wizard state is not app-owned); it lands in `preview_scroll` on
    // the next mouse event, so the release keeps the position.
    let position = crate::mouse::drag_scroll(app, crate::mouse::MouseTarget::OnboardingPreview)
        .unwrap_or(wizard.preview_scroll)
        .min(doc.saturating_sub(window) as usize) as u16;
    f.render_widget(
        Paragraph::new(lines).scroll((position, 0)),
        Rect::new(x + 2, area.y + 1, content_w, window),
    );
    // The content rect owns wheel scrolling; the gutter owns the drag.
    crate::mouse::register_region(
        app,
        Rect::new(x + 1, area.y + 1, width.saturating_sub(2), window),
        crate::mouse::MouseTarget::OnboardingPreview,
    );
    if doc > window && width > 4 {
        let gutter = Rect::new(x + width.saturating_sub(3), area.y + 1, 2, window);
        scrollbar::draw_large(
            app,
            f,
            crate::mouse::MouseTarget::OnboardingPreview,
            gutter,
            doc as usize,
            window as usize,
            position as usize,
        );
    }
}
