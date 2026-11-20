//! Bounded reads of local files for previews.

use std::io::Read;
use std::path::Path;

/// Why a bounded read returned no bytes.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Unread {
    NotFound,
    NotFile,
    TooLarge,
    Unreadable,
}

/// The bytes of `path` when it is a regular file of at most `max` bytes; a named pipe never blocks, even one swapped in after the check.
pub fn read_bounded(path: &Path, max: u64) -> Result<Vec<u8>, Unread> {
    let meta = std::fs::metadata(path).map_err(|error| match error.kind() {
        std::io::ErrorKind::NotFound => Unread::NotFound,
        _ => Unread::Unreadable,
    })?;
    if !meta.is_file() {
        return Err(Unread::NotFile);
    }
    if meta.len() > max {
        return Err(Unread::TooLarge);
    }
    let file = open(path).map_err(|_| Unread::Unreadable)?;
    if !file.metadata().is_ok_and(|meta| meta.is_file()) {
        return Err(Unread::NotFile);
    }
    let mut bytes = Vec::new();
    file.take(max + 1)
        .read_to_end(&mut bytes)
        .map_err(|_| Unread::Unreadable)?;
    match bytes.len() as u64 <= max {
        true => Ok(bytes),
        false => Err(Unread::TooLarge),
    }
}

/// Opened without waiting for a writer, which a regular file never needs.
#[cfg(unix)]
fn open(path: &Path) -> std::io::Result<std::fs::File> {
    use std::os::unix::fs::OpenOptionsExt;
    std::fs::OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_NONBLOCK)
        .open(path)
}

#[cfg(not(unix))]
fn open(path: &Path) -> std::io::Result<std::fs::File> {
    std::fs::File::open(path)
}
