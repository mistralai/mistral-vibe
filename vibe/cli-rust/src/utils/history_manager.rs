//! Prompt input history recalled with Up/Down (Python `HistoryManager`).

use std::path::PathBuf;
use std::sync::Arc;

use crate::utils::history_persist::{self, Persister, MAX_ENTRIES};
use crate::utils::paths;

pub struct HistoryManager {
    /// Off-thread read-merge-write persistence (Python `_pending_entries`).
    persister: Arc<Persister>,
    entries: Vec<String>,
    /// Navigation cursor into `entries`; `-1` means "not navigating".
    index: isize,
    /// The live input stashed when navigation starts (Python `_temp_input`).
    temp_input: String,
}

impl Default for HistoryManager {
    fn default() -> Self {
        Self {
            persister: Arc::new(Persister::new(None)),
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
        let entries = file
            .as_deref()
            .map(history_persist::read_entries)
            .unwrap_or_default();
        Self {
            persister: Arc::new(Persister::new(file)),
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
        self.persister.record(text);
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

    /// Flush pending entries via a background read-merge-write; never blocks the UI thread.
    pub fn persist(&self) {
        let persister = self.persister.clone();
        tokio::spawn(async move {
            let _ = tokio::task::spawn_blocking(move || persister.flush()).await;
        });
    }

    /// Handle for the blocking shutdown flush once the event loop has consumed the app.
    pub fn flush_handle(&self) -> Arc<Persister> {
        self.persister.clone()
    }
}

/// `$VIBE_HOME/vibehistory`, falling back to `~/.vibe/vibehistory`.
fn history_file() -> Option<PathBuf> {
    paths::vibe_home().map(|home| home.join("vibehistory"))
}
