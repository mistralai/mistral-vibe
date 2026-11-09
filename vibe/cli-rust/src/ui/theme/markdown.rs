//! Markdown colors: the chat profile's roles and the standalone widget's defaults.

use ratatui::style::{Color, Modifier, Style};

use super::{
    auto_contrast, background, blend, dim, error, foreground, is_ansi, is_dark, muted_style, text,
    text_error, text_primary, text_secondary, text_success, text_warning, warning,
};

/// Headings and table headers: `$text-primary`, or magenta on ANSI themes.
pub fn md_accent() -> Color {
    match (is_ansi(), is_dark()) {
        (false, _) => text_primary(),
        (true, true) => Color::LightMagenta,
        (true, false) => Color::Magenta,
    }
}

/// Bold headings ranked by color: H1 underlined accent, H2 accent, H3 text, H4+ muted.
pub fn md_heading(level: u8) -> Style {
    let style = match level {
        1 => text(md_accent()).add_modifier(Modifier::UNDERLINED),
        2 => text(md_accent()),
        3 => text(foreground()),
        _ => muted_style(),
    };
    style.add_modifier(Modifier::BOLD)
}

/// Table header cells.
pub fn md_table_header() -> Style {
    text(md_accent()).add_modifier(Modifier::BOLD)
}

/// Inline code: `$text-success`, darkened on light themes; ANSI follows the widget's yellow/red.
pub fn md_code_inline() -> Color {
    match (is_ansi(), is_dark()) {
        (false, true) => text_success(),
        (false, false) => blend(text_success(), auto_contrast(), 0.2),
        (true, true) => Color::Yellow,
        (true, false) => Color::Red,
    }
}

/// Unstyled code (fences, diffs, file bodies, approvals) reads like body text.
pub fn code_plain() -> Color {
    foreground()
}

/// Link label: `$text-secondary`, or the terminal blue matching the theme's brightness.
pub fn md_link() -> Color {
    match (is_ansi(), is_dark()) {
        (false, _) => text_secondary(),
        (true, true) => Color::LightBlue,
        (true, false) => Color::Blue,
    }
}

/// Table grid, horizontal rule and quote bar: `$foreground` 35%, dim text on ANSI themes.
pub fn md_keyline() -> Style {
    if is_ansi() {
        return dim(foreground());
    }
    text(blend(background(), foreground(), 0.35))
}

/// Fence box: `$foreground` 50% or bright black; selection finds it by color, so not `md_keyline`.
pub fn md_fence_border() -> Color {
    if is_ansi() {
        return Color::DarkGray;
    }
    blend(background(), foreground(), 0.5)
}

// Colors of a standalone markdown body (default styles, no chat overrides).

/// MarkdownBullet — $text-primary (dark) / $text-secondary (light).
pub fn md_widget_marker() -> Color {
    if is_dark() {
        text_primary()
    } else {
        text_secondary()
    }
}
/// MarkdownBlockQuote border-left — $text-primary 50% (dark) / $text-secondary (light).
pub fn md_widget_quote_bar() -> Color {
    if is_ansi() || !is_dark() {
        return md_widget_marker();
    }
    blend(background(), text_primary(), 0.5)
}
/// Standalone inline code: tinted warning (dark) or error (light) over its wash; ANSI keeps the raw tint.
pub fn md_widget_code_inline() -> (Color, Color) {
    if is_ansi() {
        let fg = if is_dark() { warning() } else { error() };
        return (fg, background());
    }
    let dark = is_dark();
    let wash = if dark { warning() } else { error() };
    let tint = if dark { text_warning() } else { text_error() };
    let alpha = if dark { 0.10 } else { 0.05 };
    let bg = blend(background(), wash, alpha);
    (blend(bg, tint, 0.95), bg)
}
/// Standalone MarkdownFence background — black 10% (dark) / white 30% (light).
pub fn md_widget_fence_bg() -> Option<Color> {
    if is_ansi() {
        return None;
    }
    let wash = if is_dark() {
        Color::Rgb(0, 0, 0)
    } else {
        Color::Rgb(255, 255, 255)
    };
    Some(blend(
        background(),
        wash,
        if is_dark() { 0.10 } else { 0.30 },
    ))
}
