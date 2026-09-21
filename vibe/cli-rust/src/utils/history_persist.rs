//! Concurrent-safe read-merge-write persistence for prompt history.

use std::fs;
use std::path::{Path, PathBuf};
use std::sync::Mutex;

pub const MAX_ENTRIES: usize = 100;

/// Merge pending entries onto the disk view, deduplicating at the seam (Python `persist`).
pub fn merge_entries(mut entries: Vec<String>, pending: &[String], max: usize) -> Vec<String> {
    for entry in pending {
        if entries.last().map(String::as_str) != Some(entry.as_str()) {
            entries.push(entry.clone());
        }
    }
    if entries.len() > max {
        let overflow = entries.len() - max;
        entries.drain(..overflow);
    }
    entries
}

/// Read the history file: one JSON string per line, newest `MAX_ENTRIES` kept.
pub fn read_entries(file: &Path) -> Vec<String> {
    try_read_entries(file).unwrap_or_default()
}

/// Like `read_entries`, but a failed read is an error, not an empty history (Python `_read_entries` → `None`).
fn try_read_entries(file: &Path) -> std::io::Result<Vec<String>> {
    let text = match fs::read_to_string(file) {
        Ok(text) => text,
        Err(err) if err.kind() == std::io::ErrorKind::NotFound => return Ok(Vec::new()),
        Err(err) => return Err(err),
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
    Ok(entries)
}

/// Off-thread read-merge-write half of Python's `HistoryManager`.
pub struct Persister {
    file: Option<PathBuf>,
    /// Serializes the read-merge-write (Python `_io_lock`).
    io: Mutex<()>,
    /// Entries added this session but not yet flushed (Python `_pending_entries`).
    pending: Mutex<Vec<String>>,
}

impl Persister {
    pub fn new(file: Option<PathBuf>) -> Self {
        Self {
            file,
            io: Mutex::new(()),
            pending: Mutex::new(Vec::new()),
        }
    }

    /// Queue an entry for the next flush.
    pub fn record(&self, text: &str) {
        self.pending
            .lock()
            .unwrap_or_else(|p| p.into_inner())
            .push(text.to_owned());
    }

    /// Number of entries still awaiting a flush.
    pub fn pending_len(&self) -> usize {
        self.pending.lock().unwrap_or_else(|p| p.into_inner()).len()
    }

    /// Blocking read-merge-write; call off the UI thread. Pending survives failures.
    pub fn flush(&self) {
        let Some(file) = self.file.as_deref() else {
            return;
        };
        let _io = self.io.lock().unwrap_or_else(|p| p.into_inner());
        let snapshot = {
            let pending = self.pending.lock().unwrap_or_else(|p| p.into_inner());
            if pending.is_empty() {
                return;
            }
            pending.clone()
        };
        let merged = match try_read_entries(file) {
            Ok(disk) => merge_entries(disk, &snapshot, MAX_ENTRIES),
            Err(err) => {
                tracing::warn!(%err, file = %file.display(), "history read failed; keeping pending entries");
                return;
            }
        };
        if let Err(err) = write_entries(file, &merged) {
            tracing::warn!(%err, file = %file.display(), "history persist failed");
            return;
        }
        let mut pending = self.pending.lock().unwrap_or_else(|p| p.into_inner());
        let drained = snapshot.len().min(pending.len());
        pending.drain(..drained);
    }
}

/// Atomic write (temp file + rename), one JSON string per line.
fn write_entries(file: &Path, entries: &[String]) -> std::io::Result<()> {
    if let Some(parent) = file.parent() {
        fs::create_dir_all(parent)?;
    }
    let mut body = String::new();
    for entry in entries {
        if let Ok(line) = serde_json::to_string(entry) {
            body.push_str(&line);
            body.push('\n');
        }
    }
    // Unique per process: two sessions flushing concurrently must not share the temp file (Python `mkstemp`).
    let name = file
        .file_name()
        .and_then(|n| n.to_str())
        .unwrap_or("history");
    let tmp = file.with_file_name(format!(".{name}.{}.tmp", std::process::id()));
    // Python's persist unlinks the temp file on failure; do the same so crashed
    // or failed flushes don't leave per-process tmp files behind.
    let result = fs::write(&tmp, body).and_then(|()| fs::rename(&tmp, file));
    if result.is_err() {
        let _ = fs::remove_file(&tmp);
    }
    result
}
