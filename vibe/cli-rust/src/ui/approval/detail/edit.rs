//! Bounded file-edit approval diffs.

use ratatui::text::Line;
use serde_json::Value;

use super::window::{push_prefixed_wrapped, push_styled_wrapped, Builder};
use super::{push_description, push_fields, MAX_EAGER_DETAIL_BYTES};
use crate::server::{FileEditEffectOccurrence, FileEditEffectOutput};
use crate::ui::{theme, transcript::diff};

pub(super) fn push(
    rows: &mut Builder,
    input: &Value,
    preview: Option<&FileEditEffectOutput>,
    width: u16,
) {
    push_fields(rows, input, &["filePath"], true, width);
    rows.push(Line::default());
    if let Some(output) = preview {
        if output_bytes(output) <= MAX_EAGER_DETAIL_BYTES {
            push_diff(rows, output, width);
        } else {
            push_large_output(rows, output, width);
        }
    } else if edit_bytes(input) <= MAX_EAGER_DETAIL_BYTES {
        if let Some(output) = edit_input(input) {
            push_diff(rows, &output, width);
        }
    } else {
        push_large_input(rows, input, width);
    }
    if input.get("replaceAll").and_then(Value::as_bool) == Some(true) {
        push_description(rows, "(replace_all)", width);
    } else if let Some(count) = input.get("changes").and_then(Value::as_array).map(Vec::len) {
        push_description(rows, &format!("({count} ordered changes)"), width);
    }
}

fn push_diff(rows: &mut Builder, output: &FileEditEffectOutput, width: u16) {
    let occurrences = diff::occurrences(output);
    let language = diff::language(&output.file);
    for row in diff::render_edit_diff(&occurrences, language) {
        push_styled_wrapped(rows, row.spans, width, row.band);
        if rows.done() {
            return;
        }
    }
}

fn edit_input(input: &Value) -> Option<FileEditEffectOutput> {
    let file = input
        .get("filePath")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_owned();
    if let Some(changes) = input.get("changes").and_then(Value::as_array) {
        let occurrences = changes
            .iter()
            .filter_map(|change| {
                Some(FileEditEffectOccurrence {
                    start_line: None,
                    old_text: change.get("oldString")?.as_str()?.to_owned(),
                    new_text: change.get("newString")?.as_str()?.to_owned(),
                })
            })
            .collect::<Vec<_>>();
        return (!occurrences.is_empty()).then_some(FileEditEffectOutput {
            file,
            old_string: String::new(),
            new_string: String::new(),
            occurrences,
        });
    }
    Some(FileEditEffectOutput {
        file,
        old_string: input.get("oldString")?.as_str()?.to_owned(),
        new_string: input.get("newString")?.as_str()?.to_owned(),
        occurrences: Vec::new(),
    })
}

fn edit_bytes(input: &Value) -> usize {
    let text_bytes = |value: &Value| value.as_str().map(str::len).unwrap_or(0);
    if let Some(changes) = input.get("changes").and_then(Value::as_array) {
        return changes.iter().fold(0usize, |total, change| {
            total
                .saturating_add(change.get("oldString").map(text_bytes).unwrap_or(0))
                .saturating_add(change.get("newString").map(text_bytes).unwrap_or(0))
        });
    }
    input
        .get("oldString")
        .map(text_bytes)
        .unwrap_or(0)
        .saturating_add(input.get("newString").map(text_bytes).unwrap_or(0))
}

fn push_large_input(rows: &mut Builder, input: &Value, width: u16) {
    if let Some(changes) = input.get("changes").and_then(Value::as_array) {
        for change in changes {
            push_edit_pair(rows, change, width);
            if change.get("replaceAll").and_then(Value::as_bool) == Some(true) {
                push_description(rows, "(replace_all)", width);
            }
            if rows.done() {
                return;
            }
        }
        return;
    }
    push_edit_pair(rows, input, width);
}

fn push_large_output(rows: &mut Builder, output: &FileEditEffectOutput, width: u16) {
    for occurrence in &output.occurrences {
        push_prefixed_wrapped(
            rows,
            &occurrence.old_text,
            "- ",
            width,
            theme::text(theme::error()),
        );
        push_prefixed_wrapped(
            rows,
            &occurrence.new_text,
            "+ ",
            width,
            theme::text(theme::status_ready()),
        );
        if rows.done() {
            return;
        }
    }
}

fn output_bytes(output: &FileEditEffectOutput) -> usize {
    output.occurrences.iter().fold(0usize, |total, item| {
        total
            .saturating_add(item.old_text.len())
            .saturating_add(item.new_text.len())
    })
}

fn push_edit_pair(rows: &mut Builder, input: &Value, width: u16) {
    if let Some(old) = input.get("oldString").and_then(Value::as_str) {
        push_prefixed_wrapped(rows, old, "- ", width, theme::text(theme::error()));
    }
    if let Some(new) = input.get("newString").and_then(Value::as_str) {
        push_prefixed_wrapped(rows, new, "+ ", width, theme::text(theme::status_ready()));
    }
}
