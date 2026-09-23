//! Markdown event-stream parsing: blocks, fences, autolinks and link targets.

use ratatui::style::{Modifier, Style};
use vibe_rs::ui::markdown::{parse, Block};

fn text(inline: &[(char, Style)]) -> String {
    inline.iter().map(|(c, _)| *c).collect()
}

fn heading(blocks: &[Block]) -> (u8, String) {
    match &blocks[0] {
        Block::Heading(level, inline) => (*level, text(inline)),
        _ => panic!("expected a heading"),
    }
}

#[test]
fn headings_parse_by_level() {
    let blocks = parse("# One\n## Two\n### Three\n#### Four");
    assert_eq!(heading(&blocks[..1]), (1, "One".to_string()));
    assert_eq!(heading(&blocks[1..2]), (2, "Two".to_string()));
    assert_eq!(heading(&blocks[2..3]), (3, "Three".to_string()));
    assert_eq!(heading(&blocks[3..4]), (4, "Four".to_string()));
}

#[test]
fn paragraphs_join_soft_breaks_and_collapse_whitespace() {
    let blocks = parse("first  line\nsecond line");
    let Block::Paragraph(inline) = &blocks[0] else {
        panic!("expected a paragraph");
    };
    assert_eq!(text(inline), "first line second line");
}

#[test]
fn empty_input_yields_no_blocks() {
    assert!(parse("").is_empty());
    assert!(parse("   \n\n  \t").is_empty());
}

#[test]
fn nested_lists_track_depth_and_start_number() {
    let blocks = parse("- a\n  - b\n    - c");
    let Block::List { start, items } = &blocks[0] else {
        panic!("expected a list");
    };
    assert_eq!(*start, None);
    assert_eq!(items.len(), 1);
    assert_eq!(text(&items[0].inline), "a");

    let child = items[0].children.first().expect("nested list");
    let Block::List { items: inner, .. } = child else {
        panic!("expected a nested list");
    };
    assert_eq!(text(&inner[0].inline), "b");
    let grandchild = inner[0].children.first().expect("depth 3");
    let Block::List { items: deepest, .. } = grandchild else {
        panic!("expected a depth-3 list");
    };
    assert_eq!(text(&deepest[0].inline), "c");
}

#[test]
fn ordered_lists_carry_their_start_number() {
    let blocks = parse("2. second\n3. third");
    let Block::List { start, items } = &blocks[0] else {
        panic!("expected a list");
    };
    assert_eq!(*start, Some(2));
    assert_eq!(items.len(), 2);
}

#[test]
fn code_fences_capture_language_and_body() {
    let blocks = parse("```rust\nfn main() {}\n```");
    let Block::Code(lines) = &blocks[0] else {
        panic!("expected a code block");
    };
    assert_eq!(lines.len(), 1);
    let joined: String = lines[0].iter().map(|s| s.content.clone()).collect();
    assert!(joined.contains("fn main()"));
}

#[test]
fn bare_fences_have_no_language() {
    let blocks = parse("```\nplain\n```");
    let Block::Code(lines) = &blocks[0] else {
        panic!("expected a code block");
    };
    assert!(lines[0].iter().any(|span| span.content.contains("plain")));
}

#[test]
fn fence_lang_reads_the_first_info_token() {
    use pulldown_cmark::CodeBlockKind;
    assert_eq!(
        vibe_rs::ui::markdown::fence_lang(&CodeBlockKind::Fenced("rust,no_run".into())),
        "rust"
    );
    assert_eq!(
        vibe_rs::ui::markdown::fence_lang(&CodeBlockKind::Fenced("python ignore".into())),
        "python"
    );
    assert_eq!(
        vibe_rs::ui::markdown::fence_lang(&CodeBlockKind::Indented),
        ""
    );
}

#[test]
fn fence_lines_expand_tabs_to_eight_column_stops() {
    let lines = vibe_rs::ui::markdown::fence_lines("\tlet x = 1;\n\t\treturn x;", "rust");
    let first: String = lines[0].iter().map(|s| s.content.clone()).collect();
    let second: String = lines[1].iter().map(|s| s.content.clone()).collect();
    assert_eq!(first, "        let x = 1;");
    assert_eq!(second, "                return x;");
}

#[test]
fn blockquotes_collect_their_text() {
    let blocks = parse("> quoted words");
    let Block::Quote(inline) = &blocks[0] else {
        panic!("expected a quote");
    };
    assert_eq!(text(inline), "quoted words");
}

#[test]
fn tables_split_headers_from_rows() {
    let blocks = parse("| a | b |\n| --- | --- |\n| 1 | 2 |");
    let Block::Table { headers, rows } = &blocks[0] else {
        panic!("expected a table");
    };
    assert_eq!(headers.len(), 2);
    assert_eq!(text(&headers[0]), "a");
    assert_eq!(text(&headers[1]), "b");
    assert_eq!(rows.len(), 1);
    assert_eq!(text(&rows[0][0]), "1");
    assert_eq!(text(&rows[0][1]), "2");
}

#[test]
fn bare_urls_autolink_inside_paragraphs() {
    let blocks = parse("see https://example.com/x now");
    let Block::Paragraph(inline) = &blocks[0] else {
        panic!("expected a paragraph");
    };
    let underlined: String = inline
        .iter()
        .filter(|(_, style)| style.add_modifier.contains(Modifier::UNDERLINED))
        .map(|(c, _)| *c)
        .collect();
    assert_eq!(underlined, "https://example.com/x");
}

#[test]
fn autolink_split_extracts_candidates_from_malformed_input() {
    use vibe_rs::ui::markdown::split;
    let runs = split("see (https://example.com/a/b). ok");
    assert_eq!(runs[0].0, "see (");
    assert_eq!(runs[1].0, "https://example.com/a/b");
    assert_eq!(runs[1].1.as_deref(), Some("https://example.com/a/b"));
    assert_eq!(runs[2].0, "). ok");
}

#[test]
fn autolink_trims_leading_brackets_and_trailing_punctuation() {
    use vibe_rs::ui::markdown::split;
    let runs = split("go to [example.com]!");
    assert_eq!(runs[1].0, "example.com");
    assert_eq!(runs[1].1.as_deref(), Some("http://example.com"));
}

#[test]
fn autolink_recognizes_emails_and_rejects_non_hosts() {
    use vibe_rs::ui::markdown::split;
    let email = split("mail me at jane.doe+z@example.com today");
    assert_eq!(email[1].1.as_deref(), Some("mailto:jane.doe+z@example.com"));

    assert!(split("bare http://")[0].1.is_none(), "scheme with no host");
    assert!(split("run localhost now")[0].1.is_none(), "no TLD");
    assert!(split("visit example.42")[0].1.is_none(), "numeric TLD");
    assert!(split("see example.fr/x")[1].1.is_some(), "two-letter TLD");
}

#[test]
fn link_targets_pair_labels_with_urls_in_document_order() {
    use vibe_rs::ui::markdown::targets as link_targets;
    let pairs = link_targets("[docs](https://docs.example) and [code](https://x.io)");
    assert_eq!(pairs[0], ("docs".into(), "https://docs.example".into()));
    assert_eq!(pairs[1], ("code".into(), "https://x.io".into()));
}

#[test]
fn link_targets_include_autolinks_and_code_labels() {
    use vibe_rs::ui::markdown::targets as link_targets;
    let pairs = link_targets("bare https://example.com plus [`flag`](https://c.io)");
    assert_eq!(pairs[0].0, "https://example.com");
    assert_eq!(pairs[0].1, "https://example.com");
    assert_eq!(pairs[1].0, "flag");
    assert_eq!(pairs[1].1, "https://c.io");
}

#[test]
fn link_targets_skip_empty_labels_and_breaks_join_with_spaces() {
    use vibe_rs::ui::markdown::targets as link_targets;
    let pairs = link_targets("[](https://empty) then [two\nlines](https://split)");
    assert_eq!(pairs.len(), 1);
    assert_eq!(pairs[0].0, "two lines");
}
