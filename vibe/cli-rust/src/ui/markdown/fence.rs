//! Fenced code blocks: info-string language, tab expansion, highlighted lines.

use pulldown_cmark::CodeBlockKind;
use ratatui::style::Style;
use ratatui::text::Span;
use unicode_width::UnicodeWidthChar;

use crate::ui::{highlight, theme};

/// Textual's tab stop for fence content.
const TAB_SIZE: usize = 8;

/// The fence info string's first token: ```` ```rust,no_run ```` is still Rust.
pub fn lang(kind: &CodeBlockKind) -> String {
    let CodeBlockKind::Fenced(info) = kind else {
        return String::new();
    };
    info.split([',', ' ', '\t'])
        .next()
        .unwrap_or("")
        .to_string()
}

/// Highlight the whole fence at once, falling back to plain unstyled lines.
pub fn lines(code: &str, lang: &str) -> Vec<Vec<Span<'static>>> {
    let code = expand_tabs(code);
    if let Some(lines) = highlight::code(&code, lang) {
        return lines;
    }
    let style = Style::default().fg(theme::code_plain());
    code.split('\n')
        .map(|l| vec![Span::styled(l.to_string(), style)])
        .collect()
}

/// Textual expands fence tabs to 8-column stops; a raw tab would move the cursor.
fn expand_tabs(code: &str) -> String {
    if !code.contains('\t') {
        return code.to_string();
    }
    let mut out = String::with_capacity(code.len());
    let mut col = 0;
    for c in code.chars() {
        match c {
            '\n' => {
                col = 0;
                out.push(c);
            }
            '\t' => {
                let width = TAB_SIZE - col % TAB_SIZE;
                out.extend(std::iter::repeat_n(' ', width));
                col += width;
            }
            _ => {
                col += UnicodeWidthChar::width(c).unwrap_or(0);
                out.push(c);
            }
        }
    }
    out
}
