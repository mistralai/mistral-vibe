//! Resumed inline images open through private, randomly named temporary files.

use vibe_rs::inline_images::write_private;

#[test]
fn an_inline_image_is_written_to_a_new_private_file() {
    let first = write_private(b"image bytes", "png").expect("write the image");
    let second = write_private(b"image bytes", "png").expect("write the image");

    assert_ne!(first, second, "each write gets a fresh name");
    assert_eq!(std::fs::read(&first).expect("read back"), b"image bytes");
    assert!(first
        .file_name()
        .and_then(|name| name.to_str())
        .is_some_and(|name| name.starts_with("vibe-image-") && name.ends_with(".png")));
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt as _;
        let mode = std::fs::metadata(&first)
            .expect("stat")
            .permissions()
            .mode();
        assert_eq!(mode & 0o777, 0o600);
    }
    for path in [first, second] {
        let _ = std::fs::remove_file(path);
    }
}
