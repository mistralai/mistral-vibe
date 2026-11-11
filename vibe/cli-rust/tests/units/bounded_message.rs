//! Server error and warning text is capped before the transcript wraps it (VIBE-5615).

use vibe_rs::utils::text::{bounded_message, MESSAGE_MAX_CHARS, MESSAGE_TRUNCATION_MARKER};

#[test]
fn a_short_message_is_borrowed_unchanged() {
    let message = "Upstream 502";
    assert!(matches!(
        bounded_message(message),
        std::borrow::Cow::Borrowed("Upstream 502")
    ));
}

#[test]
fn a_message_at_the_limit_is_kept_whole() {
    let message = "é".repeat(MESSAGE_MAX_CHARS);
    assert_eq!(bounded_message(&message), message);
}

#[test]
fn a_huge_message_is_cut_on_a_char_boundary_and_marked() {
    let message = "é".repeat(MESSAGE_MAX_CHARS * 300);
    assert_eq!(
        bounded_message(&message),
        format!(
            "{}{MESSAGE_TRUNCATION_MARKER}",
            "é".repeat(MESSAGE_MAX_CHARS)
        )
    );
}

#[test]
fn a_huge_inline_notice_is_capped() {
    let notice = vibe_rs::ui::notice::Notice::new(
        vibe_rs::app::Severity::Error,
        &"A".repeat(1_200_000),
        None,
    );
    assert_eq!(
        notice.text,
        format!(
            "{}{MESSAGE_TRUNCATION_MARKER}",
            "A".repeat(MESSAGE_MAX_CHARS)
        )
    );
}
