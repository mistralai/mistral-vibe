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

/// The Sessions page preview opens an image from the conversation it shows,
/// which is not the attached session's.
#[test]
fn an_inline_image_is_found_in_the_transcript_it_belongs_to() {
    use vibe_rs::inline_images::{path_in, target};
    use vibe_rs::transcript::Transcript;

    let mut previewed = Transcript::default();
    previewed.load_snapshot(
        &serde_json::from_value(serde_json::json!({
            "eventId": 1,
            "session": { "id": "other" },
            "history": [{
                "id": "m1",
                "type": "message",
                "role": "user",
                "content": [{
                    "type": "image",
                    "attachment": {
                        "alias": "pasted.png",
                        "mimeType": "image/png",
                        "source": { "kind": "inline", "data": "aW1hZ2U=" },
                    },
                }],
            }],
        }))
        .expect("state"),
    );
    let link = target("m1", 0);

    let path = path_in(&previewed, &link).expect("the previewed session has the image");
    assert_eq!(std::fs::read(&path).expect("read back"), b"image");
    let _ = std::fs::remove_file(path);
    assert!(path_in(&Transcript::default(), &link).is_none());
}
