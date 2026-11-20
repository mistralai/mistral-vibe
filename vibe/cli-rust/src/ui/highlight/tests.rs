use super::*;

const BLOCK: &str = "/* block\n   comment */\nfn main() {\n    let s = \"multi\nline\";\n    println!(\"{s}\"); // tail\n}\n";

#[test]
fn a_block_streamed_char_by_char_highlights_like_the_whole_block() {
    for end in BLOCK.char_indices().map(|(i, c)| i + c.len_utf8()) {
        let prefix = &BLOCK[..end];
        assert_eq!(
            code(prefix, "rust"),
            highlight(prefix, "rust"),
            "{prefix:?}"
        );
    }
}

#[test]
fn a_growing_block_resumes_from_its_last_complete_line() {
    let (_, resumed) = highlight_resuming("fn a() {}\n", "rust").unwrap();
    assert!(!resumed);
    let (_, resumed) = highlight_resuming("fn a() {}\nfn b", "rust").unwrap();
    assert!(resumed);
    let (_, resumed) = highlight_resuming("fn c() {}\n", "rust").unwrap();
    assert!(!resumed);
    let (_, resumed) = highlight_resuming("fn c() {}\n", "python").unwrap();
    assert!(!resumed);
}
