//! Piped-prompt parsing, mirroring Python `get_prompt_from_stdin`.
use vibe_rs::headless_prompt::{parse_piped_prompt, PipedPrompt, MAX_STDIN_PROMPT_BYTES};

#[test]
fn trims_and_returns_prompt() {
    match parse_piped_prompt(b"  hello world \n".to_vec()) {
        PipedPrompt::Prompt(text) => assert_eq!(text, "hello world"),
        _ => panic!("expected a prompt"),
    }
}

#[test]
fn whitespace_only_is_empty() {
    assert!(matches!(
        parse_piped_prompt(b"   \n\t ".to_vec()),
        PipedPrompt::Empty
    ));
}

#[test]
fn over_cap_is_too_large() {
    let bytes = vec![b'a'; MAX_STDIN_PROMPT_BYTES + 1];
    assert!(matches!(parse_piped_prompt(bytes), PipedPrompt::TooLarge));
}

#[test]
fn at_cap_is_accepted() {
    let bytes = vec![b'a'; MAX_STDIN_PROMPT_BYTES];
    assert!(matches!(parse_piped_prompt(bytes), PipedPrompt::Prompt(_)));
}

#[test]
fn split_multibyte_at_cap_reports_too_large_not_invalid() {
    // A cutoff that splits a multibyte char must report the size limit, not
    // "no prompt": the byte cap is checked before UTF-8 decoding.
    let mut bytes = vec![b'a'; MAX_STDIN_PROMPT_BYTES];
    bytes.push(0xC3); // leading byte of a 2-byte sequence, pushed past the cap
    assert!(matches!(parse_piped_prompt(bytes), PipedPrompt::TooLarge));
}

#[test]
fn invalid_utf8_within_cap_is_invalid() {
    assert!(matches!(
        parse_piped_prompt(vec![0xFF, 0xFE]),
        PipedPrompt::InvalidUtf8
    ));
}
