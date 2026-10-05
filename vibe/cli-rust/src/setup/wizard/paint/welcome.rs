//! The onboarding welcome screen (Python `WelcomeScreen`): typewriter banner
//! with a gradient highlight, in a rounded box. No petit chat, per Python.

use ratatui::layout::{Alignment, Rect};
use ratatui::style::{Color, Modifier, Style};
use ratatui::text::Text;
use ratatui::widgets::{Block, BorderType, Borders, Clear, Paragraph};
use ratatui::Frame;

use crate::setup::wizard::OnboardingState;
use crate::setup::wizard::WELCOME_TEXT;
use crate::ui::theme;
const HIGHLIGHT_START: usize = 11;
const HIGHLIGHT_END: usize = 23;
/// Python `GRADIENT_COLORS`; the browser sign-in screen paints its waiting
/// detail with the same ramp.
pub(super) const GRADIENT: [Color; 10] = [
    Color::Rgb(0xff, 0x6b, 0x00),
    Color::Rgb(0xff, 0x7b, 0x00),
    Color::Rgb(0xff, 0x8c, 0x00),
    Color::Rgb(0xff, 0x9d, 0x00),
    Color::Rgb(0xff, 0xae, 0x00),
    Color::Rgb(0xff, 0xbf, 0x00),
    Color::Rgb(0xff, 0xae, 0x00),
    Color::Rgb(0xff, 0x9d, 0x00),
    Color::Rgb(0xff, 0x8c, 0x00),
    Color::Rgb(0xff, 0x7b, 0x00),
];

/// Python `WelcomeScreen`: round border, padding 1 3, centered text, "Press Enter" hint.
pub(super) fn draw(wizard: &mut OnboardingState, f: &mut Frame, area: Rect) {
    let text = &WELCOME_TEXT[..wizard.welcome_char_index.min(WELCOME_TEXT.len())];
    // The box is text plus border and padding; narrow terminals clip it.
    let box_w = (WELCOME_TEXT.len() as u16 + 8).min(area.width);
    let box_x = area.x + (area.width.saturating_sub(box_w)) / 2;
    // Python: align center middle, margin-bottom 2 on the box, hint below.
    // Total = 5 (box) + 2 (gap) + 1 (hint) = 8 rows, centered in area height.
    let total_h = 8u16;
    let box_y = area.y + (area.height.saturating_sub(total_h)) / 2;
    // The box needs its full 5 rows: a truncated box would hand the inner
    // paragraph a rect outside the buffer.
    if area.height >= 5 {
        let box_area = Rect::new(box_x, box_y, box_w, 5.min(area.height));
        f.render_widget(Clear, box_area);
        // Repaint the theme background `Clear` just reset to the terminal default.
        f.buffer_mut()
            .set_style(box_area, Style::default().bg(theme::background()));
        f.render_widget(
            Block::default()
                .borders(Borders::ALL)
                .border_type(BorderType::Rounded)
                .border_style(Style::default().fg(theme::muted())),
            box_area,
        );
        let inner = Rect::new(
            box_area.x + 1,
            box_area.y + 2,
            box_area.width.saturating_sub(2),
            1,
        );
        f.render_widget(
            Paragraph::new(render_welcome_text(text, wizard.welcome_char_index))
                .alignment(Alignment::Center),
            inner,
        );
    }
    if !wizard.welcome_done || box_y + 7 >= area.y + area.height {
        return;
    }
    if wizard.boot_pending {
        let msg = "Starting the app server\u{2026}";
        let hx = box_x + (box_w.saturating_sub(msg.chars().count() as u16)) / 2;
        f.buffer_mut()
            .set_string(hx, box_y + 7, msg, Style::default().fg(theme::muted()));
        return;
    }
    let pre = "Press ";
    let key = "Enter";
    let post = " \u{21b5}";
    let total = (pre.len() + key.len() + post.chars().count()) as u16;
    let mut hx = box_x + (box_w.saturating_sub(total)) / 2;
    f.buffer_mut()
        .set_string(hx, box_y + 7, pre, Style::default().fg(theme::foreground()));
    hx += pre.len() as u16;
    f.buffer_mut().set_string(
        hx,
        box_y + 7,
        key,
        Style::default()
            .fg(theme::primary())
            .add_modifier(Modifier::BOLD),
    );
    hx += key.len() as u16;
    f.buffer_mut().set_string(
        hx,
        box_y + 7,
        post,
        Style::default().fg(theme::foreground()),
    );
}

fn render_welcome_text<'a>(text: &'a str, char_index: usize) -> Text<'a> {
    use ratatui::text::{Line, Span};
    if char_index <= HIGHLIGHT_START {
        return Text::from(Line::from(Span::styled(
            text,
            Style::default().fg(theme::foreground()),
        )));
    }
    let prefix = &text[..HIGHLIGHT_START.min(text.len())];
    let hl_len =
        (char_index.min(HIGHLIGHT_END) - HIGHLIGHT_START).min(text.len() - HIGHLIGHT_START);
    let highlight = &text[HIGHLIGHT_START..HIGHLIGHT_START + hl_len];
    let suffix = if char_index > HIGHLIGHT_END {
        &text[HIGHLIGHT_END..]
    } else {
        ""
    };
    let mut spans = vec![Span::styled(
        prefix,
        Style::default().fg(theme::foreground()),
    )];
    for (i, ch) in highlight.chars().enumerate() {
        spans.push(Span::styled(
            ch.to_string(),
            Style::default()
                .fg(GRADIENT[i % GRADIENT.len()])
                .add_modifier(Modifier::BOLD),
        ));
    }
    if !suffix.is_empty() {
        spans.push(Span::styled(
            suffix,
            Style::default().fg(theme::foreground()),
        ));
    }
    Text::from(Line::from(spans))
}
