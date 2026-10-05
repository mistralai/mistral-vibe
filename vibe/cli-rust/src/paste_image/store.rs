//! Python-compatible storage for pasted clipboard images.

use std::fs;
use std::io;
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

use crate::utils::datetime;

const PASTED_IMAGES_DIR: &str = "vibe-pasted-images";
const MAX_SAME_SECOND_COLLISIONS: usize = 1000;

/// Write `$TMPDIR/vibe-pasted-images/clipboard-<local timestamp>.png`, like Python.
pub fn write_clipboard_image(data: &[u8]) -> io::Result<PathBuf> {
    let dir = std::env::temp_dir().join(PASTED_IMAGES_DIR);
    fs::create_dir_all(&dir)?;
    let path = clipboard_image_path(&dir, &clipboard_timestamp(), |path| path.exists());
    fs::write(&path, data)?;
    Ok(path)
}

/// First free `clipboard-<timestamp>[-N].png`; Python falls back to the base name.
pub fn clipboard_image_path(
    dir: &Path,
    timestamp: &str,
    exists: impl Fn(&Path) -> bool,
) -> PathBuf {
    let base = dir.join(format!("clipboard-{timestamp}.png"));
    if !exists(&base) {
        return base;
    }
    (1..MAX_SAME_SECOND_COLLISIONS)
        .map(|n| dir.join(format!("clipboard-{timestamp}-{n}.png")))
        .find(|path| !exists(path))
        .unwrap_or(base)
}

/// Local `%Y%m%dT%H%M%S`.
fn clipboard_timestamp() -> String {
    let now_ms = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_or(0, |elapsed| elapsed.as_millis() as u64);
    datetime::format_local(now_ms)
        .chars()
        .take(19)
        .filter(|ch| !matches!(ch, '-' | ':'))
        .map(|ch| if ch == ' ' { 'T' } else { ch })
        .collect()
}
