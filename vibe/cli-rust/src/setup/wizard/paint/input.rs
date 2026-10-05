//! Shared input-screen pieces: the labeled input card, the validation colors,
//! the feedback row, and the "Press `<key>`" hint. Used by the custom-domain
//! and API-key screens.

use ratatui::layout::Rect;
use ratatui::style::{Color, Modifier, Style};
use ratatui::widgets::{Block, Borders, Clear};
use ratatui::Frame;

use super::panel::CARD_BORDER;
use crate::setup::wizard::{OnboardingInput, ValidationState};
use crate::ui::theme;

/// The input-card border for a validation state (Python `.invalid`, `.warning`).
pub(super) fn validation_border(validation: &ValidationState) -> Color {
    match validation {
        ValidationState::Valid => theme::success(),
        ValidationState::Invalid(_) => theme::error(),
        ValidationState::Warning(_) => theme::warning(),
        ValidationState::None => CARD_BORDER,
    }
}

/// The feedback row under an input: "Enter to submit" when valid, the message
/// when the state carries one.
pub(super) fn draw_validation_feedback(
    f: &mut Frame,
    x: u16,
    y: u16,
    width: u16,
    validation: &ValidationState,
) {
    match validation {
        ValidationState::Valid => {
            draw_press_hint(f, x, y, "Enter", " to submit \u{21b5}");
        }
        ValidationState::Invalid(msg) => {
            draw_wrapped(f, x, y, width, msg, theme::error());
        }
        ValidationState::Warning(msg) => {
            draw_wrapped(f, x, y, width, msg, theme::warning());
        }
        ValidationState::None => {}
    }
}

/// A feedback message wraps to the panel's content width (Python's Textual
/// labels wrap); the panel row grew to the wrapped line count for it.
fn draw_wrapped(
    f: &mut Frame,
    x: u16,
    y: u16,
    width: u16,
    msg: &str,
    color: ratatui::style::Color,
) {
    let style = Style::default().fg(color);
    for (offset, line) in crate::utils::text::wrap_hard(msg, width as usize)
        .iter()
        .enumerate()
    {
        f.buffer_mut().set_string(x, y + offset as u16, line, style);
    }
}

/// One labeled input card: a solid border, its title, the value or the
/// placeholder, and the cursor when the card is focused. `secure` masks the
/// value (Python `Input(password=True)`), so the caret covers a bullet.
pub(super) fn draw_input_card(
    f: &mut Frame,
    area: Rect,
    input: &OnboardingInput,
    focused: bool,
    title: &str,
    placeholder: &str,
    secure: bool,
) {
    f.render_widget(Clear, area);
    // `Clear` resets cells to the terminal default; the wizard screen is
    // theme-painted, so repaint the theme background like the other overlays
    // (Python's Textual Input renders on `$background`).
    f.buffer_mut()
        .set_style(area, Style::default().bg(theme::background()));
    let border = validation_border(&input.validation);
    f.render_widget(
        Block::default()
            .borders(Borders::ALL)
            .border_style(Style::default().fg(border)),
        area,
    );
    f.buffer_mut().set_string(
        area.x + 2,
        area.y,
        format!(" {title} "),
        Style::default()
            .fg(theme::foreground())
            .add_modifier(Modifier::BOLD),
    );
    let (value, value_style) = if secure {
        (
            "\u{2022}".repeat(input.value.chars().count()),
            Style::default().fg(theme::foreground()),
        )
    } else if input.value.is_empty() {
        (placeholder.to_owned(), theme::muted_style())
    } else {
        (
            input.value.clone(),
            Style::default().fg(theme::foreground()),
        )
    };
    f.buffer_mut()
        .set_string(area.x + 3, area.y + 1, &value, value_style);
    if focused {
        // `cursor` is a UTF-8 byte offset (the composer's edit pipeline unit);
        // the caret column and glyph are per character, so convert first. The
        // glyph under the caret comes from the displayed string, so the caret
        // covers a placeholder character when the input is empty.
        let cursor = input.cursor.min(input.value.len());
        let char_index = input.value[..cursor].chars().count();
        let offset: u16 = char_index.try_into().unwrap_or(0);
        let under = value.chars().nth(char_index).unwrap_or(' ');
        draw_caret(f, area.x + 3 + offset, area.y + 1, under);
    }
}

/// The block caret over `under` (the glyph it covers, or a blank), shared by
/// the plain and masked input cards.
pub(super) fn draw_caret(f: &mut Frame, x: u16, y: u16, under: char) {
    f.buffer_mut()
        .set_string(x, y, under.to_string(), input_cursor_style());
}

fn input_cursor_style() -> Style {
    if theme::input_cursor_reverse() {
        Style::default().fg(Color::Black).bg(Color::Gray)
    } else {
        let color = theme::input_cursor_bg();
        Style::default().fg(color).bg(color)
    }
}

/// `Press <key><suffix>`, with the key highlighted.
pub(super) fn draw_press_hint(f: &mut Frame, x: u16, y: u16, key_name: &str, suffix: &str) {
    let text = Style::default().fg(theme::foreground());
    let key = Style::default()
        .fg(theme::primary())
        .add_modifier(Modifier::BOLD);
    f.buffer_mut().set_string(x, y, "Press ", text);
    f.buffer_mut().set_string(x + 6, y, key_name, key);
    f.buffer_mut()
        .set_string(x + 6 + key_name.len() as u16, y, suffix, text);
}
