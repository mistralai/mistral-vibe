//! List cursor convention: the focused row is a bold block-cursor bar; a green `›` marks the current value.

use ratatui::layout::Rect;
use ratatui::style::{Color, Modifier, Style};
use ratatui::Frame;

use super::theme;

/// The current-value marker and its blank twin, sharing one column width.
pub(crate) const CURRENT: &str = "› ";
pub(crate) const BLANK: &str = "  ";

/// The focused row: block-cursor colors, bold.
pub(crate) fn style() -> Style {
    Style::default()
        .fg(theme::block_cursor_fg())
        .bg(theme::block_cursor_bg())
        .add_modifier(Modifier::BOLD)
}

/// Paint the cursor bar across `area`.
pub(crate) fn paint(f: &mut Frame, area: Rect) {
    f.buffer_mut().set_style(area, style());
}

/// A row's `(text, dim)` styles on `bg`: the bar style when highlighted, plain otherwise.
pub(crate) fn styles_on(highlighted: bool, bg: Color) -> (Style, Style) {
    if highlighted {
        let base = style();
        return (base, theme::dim(theme::block_cursor_fg()).patch(base));
    }
    let base = Style::default().fg(theme::foreground()).bg(bg);
    (base, theme::dim(theme::muted()).bg(bg))
}

/// [`styles_on`] over the app background.
pub(crate) fn styles(highlighted: bool) -> (Style, Style) {
    styles_on(highlighted, theme::background())
}

/// The current-value marker's style over a row's text style.
pub(crate) fn marker_style(base: Style, current: bool) -> Style {
    match current {
        true => base.fg(current_color()),
        false => base,
    }
}

/// The current-value marker's color: Rich's `green` (Textual terminal palette).
pub(crate) fn current_color() -> Color {
    theme::fixed::named(theme::fixed::Named::Green)
}

/// The `›`/blank marker for a row.
pub(crate) fn marker(current: bool) -> &'static str {
    if current {
        CURRENT
    } else {
        BLANK
    }
}

/// Whether a pre-styled row is a cursor row, whose bar then spans the full width.
pub(crate) fn is_bar(row: &[(String, Style)]) -> bool {
    !row.is_empty()
        && row
            .iter()
            .all(|(_, style)| style.bg == Some(theme::block_cursor_bg()))
}

/// `text` padded to `width` columns, so a cursor style paints the whole bar.
pub(crate) fn padded(text: &str, width: usize) -> String {
    use unicode_width::UnicodeWidthStr;
    let pad = width.saturating_sub(text.width());
    format!("{text}{}", " ".repeat(pad))
}
