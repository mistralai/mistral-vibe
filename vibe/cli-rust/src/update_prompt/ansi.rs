//! Rich-style SGR spans for the update prompt's stdout lines (Python `rprint`).

use ratatui::style::{Color, Modifier, Style};

/// One rich-markup span: styled from the active theme on a terminal without
/// `NO_COLOR`, plain otherwise (rich's own gating). The caller's startup path
/// has already activated the theme.
pub fn color_span(style: Style, text: &str) -> String {
    if std::io::IsTerminal::is_terminal(&std::io::stdout())
        && std::env::var_os("NO_COLOR").is_none()
    {
        format!("\x1b[{}m{text}\x1b[0m", sgr(style))
    } else {
        text.to_owned()
    }
}

/// The SGR parameters a style's foreground and bold render as — rich's own
/// codes for the named colors (`[green]` stays `32`).
fn sgr(style: Style) -> String {
    let mut params = Vec::new();
    if let Some(color) = style.fg {
        params.push(match color {
            Color::Reset => "39".to_owned(),
            Color::Black => "30".into(),
            Color::Red => "31".into(),
            Color::Green => "32".into(),
            Color::Yellow => "33".into(),
            Color::Blue => "34".into(),
            Color::Magenta => "35".into(),
            Color::Cyan => "36".into(),
            Color::Gray => "37".into(),
            Color::DarkGray => "90".into(),
            Color::LightRed => "91".into(),
            Color::LightGreen => "92".into(),
            Color::LightYellow => "93".into(),
            Color::LightBlue => "94".into(),
            Color::LightMagenta => "95".into(),
            Color::LightCyan => "96".into(),
            Color::White => "97".into(),
            Color::Indexed(n) => format!("38;5;{n}"),
            Color::Rgb(r, g, b) => format!("38;2;{r};{g};{b}"),
        });
    }
    if style.add_modifier.contains(Modifier::BOLD) {
        params.push("1".to_owned());
    }
    params.join(";")
}
