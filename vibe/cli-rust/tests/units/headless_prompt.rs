//! Piped-prompt and prompt-file parsing.
use vibe_rs::headless_prompt::{
    parse_piped_prompt, read_prompt_file, PipedPrompt, MAX_PROMPT_FILE_BYTES,
    MAX_STDIN_PROMPT_BYTES,
};

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

fn temp_prompt_file(name: &str, content: &[u8]) -> std::path::PathBuf {
    let dir = std::env::temp_dir().join(format!(
        "vibe-test-prompt-file-{}-{}",
        std::process::id(),
        name
    ));
    std::fs::create_dir_all(&dir).unwrap();
    let path = dir.join("prompt.txt");
    std::fs::write(&path, content).unwrap();
    path
}

fn cleanup(path: &std::path::Path) {
    if let Some(parent) = path.parent() {
        std::fs::remove_dir_all(parent).ok();
    }
}

#[test]
fn read_prompt_file_returns_trimmed_content() {
    let path = temp_prompt_file("trim", b"  hello from file  \n");
    let result = read_prompt_file(&path).unwrap();
    assert_eq!(result, "hello from file");
    cleanup(&path);
}

#[test]
fn read_prompt_file_empty_returns_empty_string() {
    let path = temp_prompt_file("empty", b"");
    let result = read_prompt_file(&path).unwrap();
    assert_eq!(result, "");
    cleanup(&path);
}

#[test]
fn read_prompt_file_whitespace_only_returns_empty_string() {
    let path = temp_prompt_file("ws", b"  \n\t  ");
    let result = read_prompt_file(&path).unwrap();
    assert_eq!(result, "");
    cleanup(&path);
}

#[test]
fn read_prompt_file_missing_returns_err() {
    let path = std::path::PathBuf::from("/nonexistent/vibe-test-prompt-file-xyz/prompt.txt");
    assert!(read_prompt_file(&path).is_err());
}

#[test]
fn read_prompt_file_at_cap_is_accepted() {
    let content = vec![b'a'; MAX_PROMPT_FILE_BYTES as usize];
    let path = temp_prompt_file("at-cap", &content);
    let result = read_prompt_file(&path);
    assert!(result.is_ok());
    cleanup(&path);
}

#[test]
fn read_prompt_file_over_cap_returns_err() {
    let content = vec![b'a'; MAX_PROMPT_FILE_BYTES as usize + 1];
    let path = temp_prompt_file("over-cap", &content);
    let result = read_prompt_file(&path);
    assert!(result.is_err());
    let err = result.unwrap_err();
    assert!(err.contains("exceeds"), "unexpected error: {err}");
    cleanup(&path);
}
