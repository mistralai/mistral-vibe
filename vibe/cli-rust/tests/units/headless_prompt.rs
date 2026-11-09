//! Piped-prompt and prompt-file parsing.
use vibe_rs::headless_prompt::{
    parse_piped_prompt, read_prompt_file, PipedPrompt, MAX_PROMPT_FILE_BYTES,
};

#[test]
fn trims_and_returns_prompt() {
    match parse_piped_prompt(b"  hello world \n".to_vec()) {
        PipedPrompt::Prompt(text) => assert_eq!(text, "hello world"),
        _ => panic!("expected a prompt"),
    }
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
