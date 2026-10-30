//! Output formatting for headless mode (text, json, streaming).

use std::collections::HashMap;
use std::io::{self, Write};

use anyhow::{bail, Result};
use serde_json::Value;

use crate::cli::OutputFormat;
use crate::server::HistoryEntry;
use crate::transcript::apply_json_patch;

const MAX_TRACKED_ENTRIES: usize = 512;

/// Headless output formatter.
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

    pub fn format(&self) -> OutputFormat {
        self.format.clone()
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

    /// Emit the final output and return the turn's answer. Returns the I/O
    /// error instead of panicking so the caller can still run session/stop
    /// cleanup on a closed pipe.
    pub fn finalize(&self, history: Value, turn_id: Option<&str>) -> io::Result<Option<String>> {
        match self.format {
            OutputFormat::Streaming => Ok(None),
            OutputFormat::Json if history.is_array() => {
                let json = serde_json::to_string_pretty(&history).unwrap_or_default();
                write_line(&json).map(|()| None)
            }
            OutputFormat::Json => Ok(None),
            OutputFormat::Text | OutputFormat::Silent => {
                let text = last_assistant_text(&turn_entries(&history, turn_id));
                if let (OutputFormat::Text, Some(text)) = (&self.format, &text) {
                    write_line(text)?;
                }
                Ok(text)
            }
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

/// The history entries of the run's turn: a reopened session's earlier runs already answered.
pub fn turn_entries(history: &Value, turn_id: Option<&str>) -> Value {
    let entries = history.as_array().into_iter().flatten();
    let turn_id = turn_id.map(Value::from);
    Value::Array(
        entries
            .filter(|entry| turn_id.is_some() && entry.get("turnId") == turn_id.as_ref())
            .cloned()
            .collect(),
    )
}

/// Last assistant text from a history array: all text blocks joined with
/// `\n\n`, like the server's `PublicMessageEntry.text` property.
pub fn last_assistant_text(history: &Value) -> Option<String> {
    history.as_array()?.iter().rev().find_map(|entry| {
        // A draft a post_agent hook rejected: shown while it streamed, never the answer.
        let retried =
            entry["outcome"]["type"] == "discarded" && entry["outcome"]["cause"] == "retry";
        let HistoryEntry::Message(message) = HistoryEntry::from_value(entry) else {
            return None;
        };
        let text = message.text("\n\n");
        (message.role == "assistant" && !retried && !text.is_empty()).then_some(text)
    })
}
