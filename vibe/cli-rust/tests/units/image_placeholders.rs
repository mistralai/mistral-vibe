//! `[Image #N]` placeholders: numbering, attachment, and prompt preparation round trip.

use vibe_rs::image_placeholders::{
    collapse, expand, is_placeholder, references, PastedImages, MAX_PASTED_IMAGES,
};
use vibe_rs::message_queue::merge_edit_images;
use vibe_rs::server::{ImageAttachment, ImageSource, PreparedPrompt};

fn file(path: &str, alias: &str) -> ImageAttachment {
    ImageAttachment {
        source: ImageSource::File { path: path.into() },
        alias: alias.into(),
        mime_type: "image/png".into(),
    }
}

#[test]
fn placeholders_number_every_paste_once() {
    let mut images = PastedImages::default();

    assert_eq!(images.register("/tmp/a.png", 0), "[Image #1]");
    assert_eq!(images.register("/tmp/a.png", 0), "[Image #2]");
    assert!(is_placeholder("[Image #2]"));
    assert!(!is_placeholder("/tmp/a.png"));
}

#[test]
fn attach_adds_only_the_placeholders_the_text_references() {
    let mut images = PastedImages::default();
    images.register("/tmp/a.png", 0);
    images.register("/tmp/b.jpg", 0);
    for _ in 0..10 {
        images.register("/tmp/other.png", 0);
    }

    let attached = images.attach("compare [Image #2] with [Image #12]", Vec::new());

    let aliases: Vec<_> = attached.iter().map(|image| image.alias.as_str()).collect();
    assert_eq!(aliases, ["[Image #2]", "[Image #12]"]);
    assert_eq!(attached[0].mime_type, "image/jpeg");
    assert_eq!(images.attach("[Image #2]", attached.clone()).len(), 2);
}

#[test]
fn the_oldest_pasted_image_is_forgotten_past_the_bound() {
    let mut images = PastedImages::default();
    for _ in 0..=MAX_PASTED_IMAGES {
        images.register("/tmp/a.png", 0);
    }

    assert!(images.attach("[Image #1]", Vec::new()).is_empty());
    assert_eq!(images.attach("[Image #2]", Vec::new()).len(), 1);
}

#[test]
fn preparation_sees_mentions_and_the_model_sees_placeholders() {
    let images = [file("/tmp/shot one.png", "[Image #1]")];
    let text = "what is in [Image #1]?";

    let expanded = expand(text, &images);
    assert_eq!(expanded, "what is in @'/tmp/shot one.png' ?");

    let mut prepared = PreparedPrompt::from_text(expanded);
    prepared.images = vec![file("/snapshots/1.png", "/tmp/shot one.png")];
    collapse(&mut prepared, text, &images);

    assert_eq!(prepared.prompt_text.as_deref(), Some(text));
    assert_eq!(prepared.images[0].alias, "[Image #1]");
    merge_edit_images(&mut prepared, &images, text);
    assert_eq!(
        prepared.images.len(),
        1,
        "the snapshot stands for the placeholder"
    );
}

#[test]
fn the_same_file_pasted_twice_shares_its_snapshot() {
    let images = [
        file("/Users/me/Desktop/shot.png", "[Image #1]"),
        file("/Users/me/Desktop/shot.png", "[Image #2]"),
    ];
    let text = "[Image #1] [Image #2]";
    let mut prepared = PreparedPrompt::from_text(expand(text, &images));
    prepared.images = vec![file("/snapshots/1.png", "/Users/me/Desktop/shot.png")];

    collapse(&mut prepared, text, &images);
    merge_edit_images(&mut prepared, &images, text);

    let sent: Vec<_> = prepared
        .images
        .iter()
        .map(|image| (image.alias.as_str(), &image.source))
        .collect();
    let snapshot = ImageSource::File {
        path: "/snapshots/1.png".into(),
    };
    assert_eq!(
        sent,
        [("[Image #1]", &snapshot), ("[Image #2]", &snapshot)],
        "no placeholder falls back to the unsent original file"
    );
}

#[test]
fn an_unprepared_placeholder_still_attaches_its_file() {
    let images = [file("/tmp/a.png", "[Image #4]")];
    let mut prepared = PreparedPrompt::from_text("see [Image #4]".into());

    merge_edit_images(&mut prepared, &images, "see [Image #4]");

    assert!(references("see [Image #4]", &images[0]));
    assert_eq!(prepared.images.len(), 1);
    assert!(!references("see nothing", &images[0]));
}

#[test]
fn labels_and_image_mentions_are_read_in_order() {
    assert_eq!(
        vibe_rs::image_placeholders::labels_in("a [Image #2] b [Image #] [Image #10]"),
        ["[Image #2]", "[Image #10]"]
    );
    assert_eq!(
        vibe_rs::paste_path::image_mentions_in(
            "see @/tmp/a.PNG, @notes.md and x@/tmp/b.png @'/tmp/c d.gif'"
        ),
        ["/tmp/a.PNG", "/tmp/c d.gif"]
    );
}

#[test]
fn a_resumed_conversation_numbers_new_pastes_past_its_placeholders() {
    let mut transcript = vibe_rs::transcript::Transcript::default();
    transcript.add(&serde_json::json!({"entry": {
        "id": "u0",
        "type": "message",
        "role": "user",
        "content": [{"type": "text", "text": "compare [Image #1] and [Image #3]"}],
    }}));
    let mut images = PastedImages::default();

    let used = transcript.highest_image_label();

    assert_eq!(used, 3);
    assert_eq!(images.register("/tmp/a.png", used), "[Image #4]");
    assert_eq!(images.register("/tmp/b.png", 0), "[Image #5]");
}

#[test]
fn history_keeps_the_image_path_behind_each_placeholder() {
    let mut images = PastedImages::default();
    images.register("/tmp/a.png", 0);

    assert_eq!(
        images.with_paths("see [Image #1] and [Image #2]"),
        "see @/tmp/a.png and [Image #2]"
    );
    assert_eq!(
        vibe_rs::image_placeholders::highest_number("[Image #2] [Image #10]"),
        10
    );
}

#[test]
fn a_placeholder_glued_to_text_still_prepares_as_a_standalone_mention() {
    let images = [file("/tmp/shot.png", "[Image #1]")];

    assert_eq!(
        expand("see[Image #1]please", &images),
        "see @/tmp/shot.png please"
    );
    assert_eq!(expand("[Image #1] ok", &images), "@/tmp/shot.png ok");
    assert_eq!(
        expand("look at [Image #1].", &images),
        "look at @/tmp/shot.png ."
    );
    assert_eq!(expand("([Image #1])", &images), "( @/tmp/shot.png )");
}

#[test]
fn numbering_saturates_instead_of_overflowing() {
    let mut images = PastedImages::default();

    assert_eq!(
        images.register("/tmp/a.png", usize::MAX),
        format!("[Image #{}]", usize::MAX)
    );
}
