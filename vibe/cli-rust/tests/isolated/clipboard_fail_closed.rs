//! copy_to_clipboard fails closed when no sink is installed.

#[test]
#[should_panic(expected = "clipboard sink installed at process start")]
fn copy_without_sink_panics() {
    vibe_rs::clipboard::copy_to_clipboard("no sink installed");
}
