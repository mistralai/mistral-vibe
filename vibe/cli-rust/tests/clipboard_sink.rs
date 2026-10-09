//! The clipboard sink delegates to the installed port implementation.

use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};

use vibe_rs::clipboard::{Clipboard, NullClipboard};

struct RecordingClipboard {
    copied: Mutex<Vec<String>>,
    verified: AtomicBool,
}

impl Clipboard for RecordingClipboard {
    fn copy(&self, text: &str) -> bool {
        self.copied
            .lock()
            .unwrap_or_else(|poison| poison.into_inner())
            .push(text.to_string());
        self.verified.load(Ordering::Relaxed)
    }
}

#[test]
fn copy_to_clipboard_uses_the_installed_sink() {
    assert!(vibe_rs::clipboard::installed_sink().is_none());
    let recording = Arc::new(RecordingClipboard {
        copied: Mutex::new(Vec::new()),
        verified: AtomicBool::new(true),
    });
    vibe_rs::clipboard::set_sink(recording.clone());
    assert!(vibe_rs::clipboard::copy_to_clipboard(
        "sink delegation guard"
    ));
    recording.verified.store(false, Ordering::Relaxed);
    assert!(!vibe_rs::clipboard::copy_to_clipboard(
        "sink delegation branch"
    ));
    let copied = recording
        .copied
        .lock()
        .unwrap_or_else(|poison| poison.into_inner());
    assert_eq!(copied.len(), 2);
    assert_eq!(copied[0], "sink delegation guard");
    assert_eq!(copied[1], "sink delegation branch");
}

#[test]
fn null_clipboard_copies_nothing() {
    assert!(!NullClipboard.copy("null sink guard"));
}
