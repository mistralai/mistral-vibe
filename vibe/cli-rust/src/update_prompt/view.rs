//! The update prompt's frames (Python `update_prompt_dialog.tcss`).

use ratatui::layout::Rect;
use ratatui::style::{Modifier, Style};
use ratatui::text::{Line, Span};
use ratatui::widgets::{Block, BorderType, Padding, Paragraph};

use super::{UpdateChoice, UpdatePromptState};
use crate::hints::{self, action, key};
use crate::ui::banner::petit_chat::PetitChat;
use crate::ui::{hint_line, list_cursor, theme};

const TITLE: &str = "A new Vibe release is available";
const HELP: &[hints::Hint] = &[(key::LEFT_RIGHT, action::NAVIGATE), hints::SELECT];
const UPDATING: &str = "Updating mistral-vibe…";
/// Gap between the two option spans.
const OPTION_GAP: &str = "    ";

pub fn draw<B: ratatui::backend::Backend>(
    terminal: &mut ratatui::Terminal<B>,
    state: &UpdatePromptState,
    chat: &mut PetitChat,
) {
    let _ = terminal.draw(|frame| {
        let area = frame.area();
        frame.buffer_mut().set_style(area, theme::screen_style());
        // `#update-dialog`: rounded `$border-blurred`, transparent inside,
        // padding 1 vertical / 5 horizontal.
        let dialog = centered(area, state.updating);
        let block = Block::bordered()
            .border_type(BorderType::Rounded)
            .border_style(Style::default().fg(theme::border_blurred()))
            .padding(Padding::new(5, 5, 1, 1));
        let inner = block.inner(dialog);
        frame.render_widget(block, dialog);
        let mut lines = vec![title(), version_line(state), Line::from("").centered()];
        if state.updating {
            // Python hides the options and help and shows the spinner instead.
            // The grid is newline-joined rows; ratatui drops a `\n` inside one
            // `Line`, so each row needs its own.
            lines.extend(chat.render().lines().map(|row| {
                Line::from(Span::styled(
                    row.to_owned(),
                    theme::text(theme::foreground()),
                ))
                .centered()
            }));
            lines.push(Line::from("").centered());
            lines.push(
                Line::from(Span::styled(UPDATING, theme::text(theme::foreground()))).centered(),
            );
        } else {
            lines.push(options(state));
            lines.push(Line::from("").centered());
            lines.push(hint_line::line(HELP).centered());
        }
        frame.render_widget(Paragraph::new(lines).centered(), inner);
    });
}

/// A dialog-sized centered rect, like Textual's `CenterMiddle`. `#update-dialog`
/// is `max-width: 70` with `height: auto` — 13 rows for the updating spinner,
/// 11 for the options, plus the border and padding.
fn centered(area: Rect, updating: bool) -> Rect {
    let width = 70.min(area.width);
    let content_rows = if updating { 13 } else { 11 };
    let height = content_rows.min(area.height);
    Rect {
        x: area.x + (area.width - width) / 2,
        y: area.y + area.height.saturating_sub(height) / 2,
        width,
        height,
    }
}

fn title() -> Line<'static> {
    Line::from(Span::styled(
        TITLE,
        Style::default()
            .fg(theme::primary())
            .add_modifier(Modifier::BOLD),
    ))
    .centered()
}

fn version_line(state: &UpdatePromptState) -> Line<'static> {
    Line::from(Span::styled(
        format!("{} → {}", state.current_version, state.latest_version),
        theme::text(theme::foreground()),
    ))
    .centered()
}

/// The two options on one row, the selected one on the list-cursor bar (Python `_refresh_options`).
fn options(state: &UpdatePromptState) -> Line<'static> {
    let mut spans = Vec::new();
    for (index, choice) in state.choices().into_iter().enumerate() {
        if index > 0 {
            spans.push(Span::raw(OPTION_GAP));
        }
        spans.push(option_span(choice, state));
    }
    Line::from(spans).centered()
}

/// One option as a padded chip, styled as the cursor bar when selected.
pub fn option_span(choice: UpdateChoice, state: &UpdatePromptState) -> Span<'static> {
    let text = format!(" {} ", choice.label(state.mode));
    match choice == state.selected {
        true => Span::styled(text, list_cursor::style()),
        false => Span::styled(text, theme::text(theme::foreground())),
    }
}
