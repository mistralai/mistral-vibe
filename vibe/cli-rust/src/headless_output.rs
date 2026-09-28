//! Output formatting for headless mode, mirroring Python `ProgrammaticOutput`.

use std::collections::HashMap;
use std::io::{self, Write};

use anyhow::{bail, Result};
use serde_json::Value;

use crate::cli::OutputFormat;
use crate::transcript::apply_json_patch;

const MAX_TRACKED_ENTRIES: usize = 512;

/// Output formatter, mirroring Python `ProgrammaticOutput`.
pub struct Output {
    format: OutputFormat,
    pending: HashMap<String, Value>,
}

impl Output {
    pub fn new(format: OutputFormat) -> Self {
        Self {
            format,
            pending: Default::default(),
        }
    }

    /// Apply one history notification and return an entry when it becomes completed.
    pub fn consume_entry(&mut self, params: &Value) -> Result<Option<Value>> {
        if self.format != OutputFormat::Streaming {
            return Ok(None);
        }
        if let Some(entry) = params.get("entry") {
            return self.add_entry(entry);
        }
        self.update_entry(params)
    }

    fn add_entry(&mut self, entry: &Value) -> Result<Option<Value>> {
        let Some(id) = entry.get("id").and_then(Value::as_str) else {
            return Ok(None);
        };
        if is_completed(entry) {
            return Ok(Some(entry.clone()));
        }
        if !self.pending.contains_key(id) && self.pending.len() >= MAX_TRACKED_ENTRIES {
            bail!("streaming entry limit reached ({MAX_TRACKED_ENTRIES})");
        }
        self.pending.insert(id.to_owned(), entry.clone());
        Ok(None)
    }

    fn update_entry(&mut self, params: &Value) -> Result<Option<Value>> {
        let Some(id) = params.get("entryId").and_then(Value::as_str) else {
            return Ok(None);
        };
        let Some(entry) = self.pending.get_mut(id) else {
            return Ok(None);
        };
        if let Some(ops) = params.get("patch").and_then(Value::as_array) {
            apply_json_patch(entry, ops);
        }
        if is_completed(entry) {
            return Ok(self.pending.remove(id));
        }
        Ok(None)
    }

    /// Write one entry. Returns the I/O error (e.g. BrokenPipe from `| head`)
    /// instead of panicking, so session/stop cleanup still runs.
    pub fn emit_entry(&self, entry: &Value) -> io::Result<()> {
        let line = serde_json::to_string(entry).unwrap_or_default();
        write_line(&line)
    }

    /// Emit the final output. Returns the I/O error instead of panicking so the
    /// caller can still run session/stop cleanup on a closed pipe.
    pub fn finalize(&self, history: Value) -> io::Result<()> {
        match self.format {
            OutputFormat::Streaming => Ok(()),
            OutputFormat::Json => {
                if let Some(history_arr) = history.as_array() {
                    let payload = Value::Array(history_arr.clone());
                    let json = serde_json::to_string_pretty(&payload).unwrap_or_default();
                    return write_line(&json);
                }
                Ok(())
            }
            OutputFormat::Text => match last_assistant_text(&history) {
                Some(text) => write_line(&text),
                None => Ok(()),
            },
        }
    }
}

fn write_line(line: &str) -> io::Result<()> {
    let mut out = io::stdout();
    writeln!(out, "{line}")?;
    out.flush()
}

fn is_completed(entry: &Value) -> bool {
    entry.get("generationStatus").and_then(Value::as_str) == Some("completed")
}

/// Last assistant text from a history array, mirroring `_last_assistant_text`.
/// Joins all text blocks with `\n\n`, matching the Python `PublicMessageEntry.text` property.
pub fn last_assistant_text(history: &Value) -> Option<String> {
    let entries = history.as_array()?;
    entries.iter().rev().find_map(|entry| {
        if entry.get("type").and_then(Value::as_str) != Some("message") {
            return None;
        }
        if entry.get("role").and_then(Value::as_str) != Some("assistant") {
            return None;
        }
        let blocks = entry.get("content").and_then(Value::as_array)?;
        let text: Vec<String> = blocks
            .iter()
            .filter_map(|b| {
                if b.get("type").and_then(Value::as_str) == Some("text") {
                    b.get("text").and_then(Value::as_str).map(str::to_owned)
                } else {
                    None
                }
            })
            .collect();
        let joined = text.join("\n\n");
        (!joined.is_empty()).then_some(joined)
    })
}
