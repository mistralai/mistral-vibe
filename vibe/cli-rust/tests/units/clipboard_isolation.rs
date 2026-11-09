//! The units binary must never reach the host clipboard.

#[test]
fn units_binary_uses_the_null_clipboard_sink() {
    assert!(vibe_rs::clipboard::installed_sink().is_some());
    assert!(!vibe_rs::clipboard::copy_to_clipboard(
        "clipboard isolation guard"
    ));
}
