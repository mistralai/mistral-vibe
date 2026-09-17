//! Off-thread contextual previews for file-edit approvals.

use std::io::Read;
use std::path::{Path, PathBuf};

use serde_json::Value;

use super::Event;
use crate::app::App;
use crate::server::{FileEditEffectOccurrence, FileEditEffectOutput};

const MAX_FILE_BYTES: u64 = 4 * 1024 * 1024;
const MAX_OCCURRENCES: usize = 1_024;
const MAX_OUTPUT_BYTES: usize = 4 * 1024 * 1024;

pub(super) fn load(app: &App) {
    let Some(callback) = app.approval.active.as_ref() else {
        return;
    };
    if callback.detail.effect.kind != "file_edit" {
        return;
    }
    let callback_id = callback.callback_id.clone();
    let input = callback.detail.effect.input.clone();
    let cwd = app.session.cwd.clone();
    let tx = app.approval.tx.clone();
    let pending = app.commit_started();
    tokio::spawn(async move {
        let output = tokio::task::spawn_blocking(move || build(&input, cwd.as_deref()))
            .await
            .ok()
            .flatten();
        crate::input::deliver(
            tx,
            Event::Preview {
                callback_id,
                output,
            },
            &pending,
        )
        .await;
    });
}

fn build(input: &Value, cwd: Option<&str>) -> Option<FileEditEffectOutput> {
    let file = input.get("filePath")?.as_str()?.to_owned();
    let path = resolve(&file, cwd);
    let handle = std::fs::File::open(path).ok()?;
    if handle.metadata().ok()?.len() > MAX_FILE_BYTES {
        return None;
    }
    let mut content = String::new();
    handle
        .take(MAX_FILE_BYTES + 1)
        .read_to_string(&mut content)
        .ok()?;
    if content.len() as u64 > MAX_FILE_BYTES {
        return None;
    }
    let occurrences = match input.get("changes").and_then(Value::as_array) {
        Some(changes) => batch_occurrences(&mut content, changes)?,
        None => single_occurrences(&content, input)?,
    };
    if occurrence_bytes(&occurrences) > MAX_OUTPUT_BYTES {
        return None;
    }
    Some(FileEditEffectOutput {
        file,
        old_string: String::new(),
        new_string: String::new(),
        occurrences,
    })
}

fn resolve(file: &str, cwd: Option<&str>) -> PathBuf {
    let path = Path::new(file);
    if path.is_absolute() {
        path.to_owned()
    } else {
        cwd.map(Path::new)
            .unwrap_or_else(|| Path::new("."))
            .join(path)
    }
}

fn single_occurrences(content: &str, input: &Value) -> Option<Vec<FileEditEffectOccurrence>> {
    let old = input.get("oldString")?.as_str()?;
    let new = input.get("newString")?.as_str()?;
    let replace_all = input
        .get("replaceAll")
        .and_then(Value::as_bool)
        .unwrap_or(false);
    occurrences(content, old, new, replace_all)
}

fn batch_occurrences(
    content: &mut String,
    changes: &[Value],
) -> Option<Vec<FileEditEffectOccurrence>> {
    let mut output = Vec::new();
    for change in changes {
        let old = change.get("oldString")?.as_str()?;
        let new = change.get("newString")?.as_str()?;
        let replace_all = change
            .get("replaceAll")
            .and_then(Value::as_bool)
            .unwrap_or(false);
        let mut found = occurrences(content, old, new, replace_all)?;
        if output.len().saturating_add(found.len()) > MAX_OCCURRENCES
            || occurrence_bytes(&output).saturating_add(occurrence_bytes(&found)) > MAX_OUTPUT_BYTES
        {
            return None;
        }
        output.append(&mut found);
        if !old.trim_matches('\n').is_empty() {
            *content = if replace_all {
                content.replace(old, new)
            } else {
                content.replacen(old, new, 1)
            };
        }
    }
    Some(output)
}

fn occurrences(
    content: &str,
    old: &str,
    new: &str,
    replace_all: bool,
) -> Option<Vec<FileEditEffectOccurrence>> {
    let mut output = Vec::new();
    if old.trim_matches('\n').is_empty() {
        return Some(vec![bare(old, new)]);
    }
    let mut cursor = 0;
    while let Some(relative) = content[cursor..].find(old) {
        let position = cursor + relative;
        output.push(context(content, position, old, new));
        if occurrence_bytes(&output) > MAX_OUTPUT_BYTES {
            return None;
        }
        if !replace_all {
            break;
        }
        cursor = position.saturating_add(old.len());
        if output.len() >= MAX_OCCURRENCES && content[cursor..].contains(old) {
            return None;
        }
    }
    if output.is_empty() {
        output.push(bare(old, new));
    }
    Some(output)
}

fn occurrence_bytes(items: &[FileEditEffectOccurrence]) -> usize {
    items.iter().fold(0usize, |total, item| {
        total
            .saturating_add(item.old_text.len())
            .saturating_add(item.new_text.len())
    })
}

fn context(content: &str, position: usize, old: &str, new: &str) -> FileEditEffectOccurrence {
    let start_line = content[..position]
        .bytes()
        .filter(|byte| *byte == b'\n')
        .count()
        .saturating_add(1)
        .try_into()
        .unwrap_or(u32::MAX);
    let line_start = content[..position]
        .rfind('\n')
        .map(|index| index + 1)
        .unwrap_or(0);
    let end = position + old.len();
    let line_end = if content[..end].ends_with('\n') {
        end
    } else {
        content[end..]
            .find('\n')
            .map(|index| end + index)
            .unwrap_or(content.len())
    };
    let prefix = &content[line_start..position];
    let suffix = &content[end..line_end];
    FileEditEffectOccurrence {
        start_line: Some(start_line),
        old_text: format!("{prefix}{old}{suffix}"),
        new_text: format!("{prefix}{new}{suffix}"),
    }
}

fn bare(old: &str, new: &str) -> FileEditEffectOccurrence {
    FileEditEffectOccurrence {
        start_line: None,
        old_text: old.to_owned(),
        new_text: new.to_owned(),
    }
}
