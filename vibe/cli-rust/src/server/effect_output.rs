//! Result-body formatting for settled effects (Python result widgets' compose).

use serde::Deserialize;
use serde_json::Value;

use crate::utils::clean::clean_output;

/// Python `FileEditEffectOutput`: the whole-line occurrences the diff view renders.
#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct FileEditEffectOutput {
    /// The edited path; its extension picks the diff's highlight language.
    #[serde(default)]
    pub file: String,
    #[serde(default)]
    pub old_string: String,
    #[serde(default)]
    pub new_string: String,
    #[serde(default)]
    pub occurrences: Vec<FileEditEffectOccurrence>,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct FileEditEffectOccurrence {
    #[serde(default)]
    pub start_line: Option<u32>,
    #[serde(default)]
    pub old_text: String,
    #[serde(default)]
    pub new_text: String,
}

/// Python `TodoResultWidget`: one rendered todo row and its status bucket.
pub struct TodoRow<'a> {
    pub status: &'a str,
    pub text: String,
}

/// Result body lines shown on expand, formatted from the full public output.
pub fn format_effect_output(kind: Option<&str>, output: Option<&Value>) -> Vec<String> {
    let Some(output) = output else {
        return Vec::new();
    };
    match kind {
        Some("file_read") => cleaned_text(output, "content")
            .into_iter()
            .map(|line| strip_line_number(&line))
            .collect(),
        // Rendered as a diff, so there is no text body.
        Some("file_edit") => Vec::new(),
        Some("file_write") => cleaned_text(output, "content"),
        // Python renders these through `_yield_text`, which sanitizes first.
        Some("web_fetch") => cleaned_text(output, "content"),
        Some("file_search") => cleaned_text(output, "matches"),
        Some("shell") => shell_lines(output),
        Some("web_search") => web_search_lines(output),
        // Python's `AskUserQuestionResultWidget` composes nothing: the answer is
        // already on the call line, so the raw result must never be dumped.
        Some("user_question") => Vec::new(),
        Some("subagent") => python_json_lines(Some(output)),
        Some("todo") => todo_rows(output).into_iter().map(|row| row.text).collect(),
        _ => {
            let content = cleaned_text(output, "content");
            if content.is_empty() {
                json_lines(output)
            } else {
                content
            }
        }
    }
}

/// Python `BashResultWidget`: an output with nothing to show keeps the body
/// mountable through the `(no content)` fallback line.
fn shell_lines(output: &Value) -> Vec<String> {
    let transcript = shell_transcript(output);
    let lines = lines(&clean_output(&transcript));
    if lines.is_empty() || lines.iter().all(String::is_empty) {
        return vec!["(no content)".to_string()];
    }
    lines
}

/// Python `ShellEffectOutput.transcript`: the arrival-ordered `output` when set,
/// else `stdout`/`stderr` joined without fabricating a line.
fn shell_transcript(output: &Value) -> String {
    let raw = |key: &str| output.get(key).and_then(Value::as_str).unwrap_or("");
    if !raw("output").is_empty() {
        return raw("output").to_owned();
    }
    let (stdout, stderr) = (raw("stdout"), raw("stderr"));
    if !stdout.is_empty() && !stderr.is_empty() && !stdout.ends_with('\n') {
        return format!("{stdout}\n{stderr}");
    }
    format!("{stdout}{stderr}")
}

/// Python `TodoResultWidget`: todos bucketed by status in a fixed order, one
/// `{icon} {content}` row each; an empty list renders `No todos`.
pub fn todo_rows(output: &Value) -> Vec<TodoRow<'_>> {
    let Some(todos) = output
        .get("todos")
        .and_then(Value::as_array)
        .filter(|todos| !todos.is_empty())
    else {
        return vec![TodoRow {
            status: "empty",
            text: "No todos".to_string(),
        }];
    };
    const ORDER: [(&str, &str); 4] = [
        ("in_progress", "☐"),
        ("pending", "☐"),
        ("completed", "☑"),
        ("cancelled", "☒"),
    ];
    let mut rows = Vec::new();
    for (status, icon) in ORDER {
        for todo in todos {
            if todo.get("status").and_then(Value::as_str) == Some(status) {
                let content = todo.get("content").and_then(Value::as_str).unwrap_or("");
                rows.push(TodoRow {
                    status,
                    text: format!("{icon} {content}"),
                });
            }
        }
    }
    rows
}

/// The `_yield_text` path: sanitized before splitting into body rows.
fn cleaned_text(output: &Value, key: &str) -> Vec<String> {
    output
        .get(key)
        .and_then(Value::as_str)
        .map(|text| lines(&clean_output(text)))
        .unwrap_or_default()
}

fn web_search_lines(output: &Value) -> Vec<String> {
    let mut lines = output
        .get("query")
        .and_then(Value::as_str)
        .map(|query| vec![format!("query: {query}")])
        .unwrap_or_default();
    if let Some(answer) = output.get("answer").and_then(Value::as_str) {
        lines.extend(lines_with_prefix("answer: ", &clean_output(answer)));
    }
    if let Some(sources) = output.get("sources").and_then(Value::as_array) {
        if !sources.is_empty() {
            lines.push(String::new());
        }
        if sources.len() > 1 {
            lines.push("Sources:".to_string());
        }
        lines.extend(
            sources
                .iter()
                .filter_map(source_label_url)
                .map(|(label, _)| format!("  • {label}")),
        );
    }
    lines
}

/// Python labels a source with its title, falling back to the bare URL.
pub(super) fn source_label_url(source: &Value) -> Option<(String, String)> {
    let url = source
        .get("url")
        .and_then(Value::as_str)
        .unwrap_or_default();
    let label = source
        .get("title")
        .and_then(Value::as_str)
        .filter(|title| !title.is_empty())
        .unwrap_or(url);
    (!label.is_empty()).then(|| (label.to_owned(), url.to_owned()))
}

/// Python `_format_generic_result` then `_yield_text`: format the payload,
/// sanitize, split.
fn json_lines(value: &Value) -> Vec<String> {
    let formatted = match value {
        Value::String(text) => text.clone(),
        Value::Object(values) => values
            .iter()
            .filter(|(_, value)| !value.is_null() && *value != &Value::String(String::new()))
            .map(|(key, value)| format!("{key}: {}", json_value(value)))
            .collect::<Vec<_>>()
            .join("\n"),
        _ => json_value(value),
    };
    lines(&clean_output(&formatted))
}

/// Python generic subagent widget: format fields, then `_yield_text`.
pub(super) fn python_json_lines(output: Option<&Value>) -> Vec<String> {
    let Some(output) = output else {
        return Vec::new();
    };
    let formatted = match output {
        Value::Object(values) => values
            .iter()
            .filter(|(_, value)| {
                !value.is_null()
                    && *value != &Value::String(String::new())
                    && !matches!(value, Value::Array(items) if items.is_empty())
            })
            .map(|(key, value)| format!("{key}: {}", python_json_value(value)))
            .collect::<Vec<_>>()
            .join("\n"),
        _ => python_json_value(output),
    };
    lines(&clean_output(&formatted))
}

fn python_json_value(value: &Value) -> String {
    match value {
        Value::String(text) => text.clone(),
        Value::Bool(value) => if *value { "True" } else { "False" }.to_owned(),
        Value::Null => "None".to_owned(),
        Value::Number(number) => number.to_string(),
        Value::Array(items) => format!(
            "[{}]",
            items
                .iter()
                .map(python_json_repr)
                .collect::<Vec<_>>()
                .join(", ")
        ),
        Value::Object(values) => format!(
            "{{{}}}",
            values
                .iter()
                .map(|(key, value)| format!("{}: {}", python_string(key), python_json_repr(value)))
                .collect::<Vec<_>>()
                .join(", ")
        ),
    }
}

fn python_json_repr(value: &Value) -> String {
    match value {
        Value::String(text) => python_string(text),
        _ => python_json_value(value),
    }
}

fn python_string(value: &str) -> String {
    let escaped = value
        .replace('\\', "\\\\")
        .replace('\'', "\\'")
        .replace('\n', "\\n")
        .replace('\r', "\\r");
    format!("'{escaped}'")
}

fn json_value(value: &Value) -> String {
    match value {
        Value::String(text) => text.clone(),
        _ => serde_json::to_string_pretty(value).unwrap_or_default(),
    }
}

fn lines(text: &str) -> Vec<String> {
    text.lines().map(str::to_owned).collect()
}

fn lines_with_prefix(prefix: &str, text: &str) -> Vec<String> {
    let mut lines = lines(text);
    if let Some(first) = lines.first_mut() {
        first.insert_str(0, prefix);
    }
    lines
}

fn strip_line_number(line: &str) -> String {
    let trimmed = line.trim_start_matches(' ');
    let digits = trimmed.chars().take_while(char::is_ascii_digit).count();
    trimmed
        .strip_prefix(&trimmed[..digits])
        .and_then(|rest| rest.strip_prefix('→'))
        .map(str::to_owned)
        .unwrap_or_else(|| line.to_owned())
}
