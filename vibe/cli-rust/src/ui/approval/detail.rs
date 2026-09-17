//! Tool-specific approval detail rendering.

mod edit;
mod window;

use ratatui::text::{Line, Span};
use serde_json::Value;
use unicode_width::UnicodeWidthStr;

use crate::server::{ApprovalEffect, FileEditEffectOutput};
use crate::ui::{highlight, markdown, theme};
use window::{push_styled_wrapped, push_wrapped, Builder, Rows};

pub(super) const MAX_EAGER_DETAIL_BYTES: usize = 64 * 1024;
const MAX_EAGER_DETAIL_LINES: usize = 2_048;

pub(super) fn window(
    effect: &ApprovalEffect,
    preview: Option<&FileEditEffectOutput>,
    width: u16,
    start: usize,
    count: usize,
    known_total: Option<usize>,
) -> Rows {
    let mut rows = Builder::new(start, count, known_total);
    match effect.kind.as_str() {
        "shell" => {
            if let Some(command) = effect.input.get("command").and_then(Value::as_str) {
                push_code(&mut rows, command, "bash", width);
            }
        }
        "file_read" => push_fields(
            &mut rows,
            &effect.input,
            &["filePath", "offset", "limit"],
            false,
            width,
        ),
        "file_search" => push_fields(
            &mut rows,
            &effect.input,
            &["pattern", "path", "maxMatches"],
            false,
            width,
        ),
        "todo" => push_todo(&mut rows, &effect.input, width),
        "file_write" => push_file_write(&mut rows, &effect.input, width),
        "file_edit" => edit::push(&mut rows, &effect.input, preview, width),
        _ => push_generic_fields(&mut rows, &effect.input, width),
    }
    rows.finish()
}

fn push_file_write(rows: &mut Builder, input: &Value, width: u16) {
    push_fields(rows, input, &["filePath"], true, width);
    rows.push(Line::default());
    if let Some(content) = input.get("content").and_then(Value::as_str) {
        push_code(rows, content, "text", width);
    }
}

fn push_code(rows: &mut Builder, content: &str, language: &str, width: u16) {
    if content.len() > MAX_EAGER_DETAIL_BYTES
        || content.lines().take(MAX_EAGER_DETAIL_LINES + 1).count() > MAX_EAGER_DETAIL_LINES
    {
        push_large_code(rows, content, language, width);
        return;
    }
    for spans in markdown::code_lines(content, language) {
        push_styled_wrapped(rows, spans, width, None);
        if rows.done() {
            return;
        }
    }
}

fn push_large_code(rows: &mut Builder, content: &str, language: &str, width: u16) {
    let style = theme::text(theme::code_plain());
    for line in content.split('\n') {
        if line.width() <= usize::from(width.max(1)) {
            rows.push_with(|| {
                let spans = highlight::code(line, language)
                    .and_then(|mut lines| lines.pop())
                    .unwrap_or_else(|| vec![Span::styled(line.to_owned(), style)]);
                Line::from(spans)
            });
        } else {
            push_styled_wrapped(
                rows,
                vec![Span::styled(line.to_owned(), style)],
                width,
                None,
            );
        }
        if rows.done() {
            return;
        }
    }
}

fn push_todo(rows: &mut Builder, input: &Value, width: u16) {
    push_fields(rows, input, &["action"], false, width);
    if let Some(count) = input.get("todos").and_then(Value::as_array).map(Vec::len) {
        push_description(rows, &format!("Todos: {count} items"), width);
    }
}

fn push_fields(
    rows: &mut Builder,
    input: &Value,
    names: &[&str],
    title_case_path: bool,
    width: u16,
) {
    for name in names {
        let Some(value) = input.get(name) else {
            continue;
        };
        if value.is_null() || value == "" || value.as_array().is_some_and(Vec::is_empty) {
            continue;
        }
        let label = if title_case_path && *name == "filePath" {
            "File".to_owned()
        } else {
            snake_case(name)
        };
        push_value(rows, &label, value, width);
        if rows.done() {
            return;
        }
    }
}

fn push_generic_fields(rows: &mut Builder, input: &Value, width: u16) {
    let Some(fields) = input.as_object() else {
        push_description(rows, &display_value(input), width);
        return;
    };
    for (name, value) in fields {
        if !value.is_null() && value != "" {
            push_value(rows, &snake_case(name), value, width);
            if rows.done() {
                return;
            }
        }
    }
}

fn push_value(rows: &mut Builder, label: &str, value: &Value, width: u16) {
    push_description(rows, &format!("{label}: {}", display_value(value)), width);
}

fn display_value(value: &Value) -> String {
    value
        .as_str()
        .map(str::to_owned)
        .unwrap_or_else(|| value.to_string())
}

fn snake_case(name: &str) -> String {
    let mut out = String::with_capacity(name.len());
    for ch in name.chars() {
        if ch.is_ascii_uppercase() {
            out.push('_');
            out.push(ch.to_ascii_lowercase());
        } else {
            out.push(ch);
        }
    }
    out
}

fn push_description(rows: &mut Builder, text: &str, width: u16) {
    push_wrapped(rows, text, width, theme::muted_style());
}
