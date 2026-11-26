//! Size-capped log file

use std::fs::{File, OpenOptions};
use std::io::Write;
use std::path::{Path, PathBuf};

// Default maximum log file size in bytes (10 MB)
pub const DEFAULT_LOG_MAX_BYTES: u64 = 10 * 1024 * 1024;

pub struct RotatingFile {
    path: PathBuf,
    max_bytes: u64,
    file: File,
    size: u64,
}

impl RotatingFile {
    pub fn open(path: &Path, max_bytes: u64) -> std::io::Result<Self> {
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent)?;
        }
        let file = OpenOptions::new().create(true).append(true).open(path)?;
        let size = file.metadata().map(|meta| meta.len()).unwrap_or(0);
        Ok(Self {
            path: path.to_path_buf(),
            max_bytes,
            file,
            size,
        })
    }

    pub fn write_line(&mut self, line: &str) {
        let _ = writeln!(self, "{line}");
    }

    fn rollover(&mut self) -> std::io::Result<()> {
        self.file = OpenOptions::new()
            .create(true)
            .write(true)
            .truncate(true)
            .open(&self.path)?;
        self.size = 0;
        Ok(())
    }
}

/// Truncates before a write that would cross the cap, never mid-write.
impl Write for RotatingFile {
    fn write(&mut self, buf: &[u8]) -> std::io::Result<usize> {
        if self.max_bytes > 0 && self.size + buf.len() as u64 >= self.max_bytes {
            self.rollover()?;
        }
        let written = self.file.write(buf)?;
        self.size += written as u64;
        Ok(written)
    }

    fn flush(&mut self) -> std::io::Result<()> {
        self.file.flush()
    }
}
