//! Prompt input history recalled with Up/Down (Python `HistoryManager`).

use std::fs;
use std::path::PathBuf;

use crate::utils::paths;

const MAX_ENTRIES: usize = 100;

pub struct HistoryManager {
    file: Option<PathBuf>,
    entries: Vec<String>,
    /// Navigation cursor into `entries`; `-1` means "not navigating".
    index: isize,
    /// The live input stashed when navigation starts (Python `_temp_input`).
    temp_input: String,
}

impl Default for HistoryManager {
    fn default() -> Self {
        Self {
            file: None,
            entries: Vec::new(),
            index: -1,
            temp_input: String::new(),
        }
    }
}

impl HistoryManager {
    /// Load entries from `$VIBE_HOME/vibehistory`, keeping the newest `MAX_ENTRIES`.
    pub fn load() -> Self {
        let file = history_file();
        let entries = file.as_deref().map(read_entries).unwrap_or_default();
        Self {
            file,
            entries,
            index: -1,
            temp_input: String::new(),
        }
    }

    /// Record a submitted prompt, skipping blanks and immediate repeats.
    pub fn add(&mut self, text: &str) {
        let text = text.trim();
        if text.is_empty() || self.entries.last().map(String::as_str) == Some(text) {
            return;
        }
        self.entries.push(text.to_owned());
        if self.entries.len() > MAX_ENTRIES {
            let overflow = self.entries.len() - MAX_ENTRIES;
            self.entries.drain(..overflow);
        }
    }

    /// Walk one step toward older entries; returns `None` at the oldest.
    pub fn get_previous(&mut self, current_input: &str) -> Option<String> {
        if self.entries.is_empty() {
            return None;
        }
        let len = self.entries.len() as isize;
        if self.index == -1 {
            self.temp_input = current_input.to_owned();
            self.index = len;
        } else if self.index > len {
            self.index = len;
        }
        if self.index <= 0 {
            return None;
        }
        self.index -= 1;
        Some(self.entries[self.index as usize].clone())
    }

    /// Walk one step toward newer entries; returns `None` when not navigating.
    pub fn get_next(&mut self) -> Option<String> {
        if self.index == -1 {
            return None;
        }
        let len = self.entries.len() as isize;
        if self.index < len - 1 {
            self.index += 1;
            return Some(self.entries[self.index as usize].clone());
        }
        self.index = -1;
        Some(std::mem::take(&mut self.temp_input))
    }

    pub fn reset_navigation(&mut self) {
        self.index = -1;
        self.temp_input.clear();
    }

    pub fn is_navigating(&self) -> bool {
        self.index != -1
    }

    /// The recorded entries, newest last.
    pub fn entries(&self) -> &[String] {
        &self.entries
    }

    /// Persist all entries atomically (temp file + rename), one JSON string per line.
    pub fn persist(&self) {
        let Some(file) = self.file.as_deref() else {
            return;
        };
        if let Some(parent) = file.parent() {
            let _ = fs::create_dir_all(parent);
        }
        let mut body = String::new();
        for entry in &self.entries {
            if let Ok(line) = serde_json::to_string(entry) {
                body.push_str(&line);
                body.push('\n');
            }
        }
        let tmp = file.with_extension("tmp");
        if let Err(err) = fs::write(&tmp, body).and_then(|()| fs::rename(&tmp, file)) {
            tracing::warn!(%err, file = %file.display(), "history persist failed");
        }
    }
}

/// `$VIBE_HOME/vibehistory`, falling back to `~/.vibe/vibehistory`.
fn history_file() -> Option<PathBuf> {
    paths::vibe_home().map(|home| home.join("vibehistory"))
}

/// Read the history file: one JSON string per line, newest `MAX_ENTRIES` kept.
fn read_entries(file: &std::path::Path) -> Vec<String> {
    let Ok(text) = fs::read_to_string(file) else {
        return Vec::new();
    };
    let mut entries: Vec<String> = text
        .lines()
        .filter(|l| !l.is_empty())
        .map(|l| serde_json::from_str::<String>(l).unwrap_or_else(|_| l.to_owned()))
        .collect();
    if entries.len() > MAX_ENTRIES {
        let overflow = entries.len() - MAX_ENTRIES;
        entries.drain(..overflow);
    }
    entries
}
