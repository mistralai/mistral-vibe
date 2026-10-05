//! Markdown event-stream parsing: blocks, fences, autolinks, rules, tasks, images, and styles.

use ratatui::style::Modifier;
use vibe_rs::ui::markdown::{parse, render, Block, Sc};
use vibe_rs::ui::theme;

fn text(inline: &[Sc]) -> String {
    inline.iter().map(|sc| sc.0).collect()
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
        .filter(|sc| sc.1.add_modifier.contains(Modifier::UNDERLINED))
        .map(|sc| sc.0)
        .collect();
    assert_eq!(underlined, "https://example.com/x");
}

#[test]
fn strikethrough_styles_text_without_markers() {
    let blocks = parse("~~gone~~");
    let Block::Paragraph(inline) = &blocks[0] else {
        panic!("expected a paragraph");
    };
    assert_eq!(text(inline), "gone");
    assert!(inline
        .iter()
        .all(|sc| sc.1.add_modifier.contains(Modifier::CROSSED_OUT)));
}

#[test]
fn strikethrough_composes_with_emphasis() {
    let blocks = parse("~~a *b*~~");
    let Block::Paragraph(inline) = &blocks[0] else {
        panic!("expected a paragraph");
    };
    assert_eq!(text(inline), "a b");
    assert!(inline
        .iter()
        .all(|sc| sc.1.add_modifier.contains(Modifier::CROSSED_OUT)));
    let b: String = inline
        .iter()
        .filter(|sc| sc.1.add_modifier.contains(Modifier::ITALIC))
        .map(|sc| sc.0)
        .collect();
    assert_eq!(b, "b");
}

#[test]
fn rules_parse_between_paragraphs() {
    let blocks = parse("a\n\n---\n\nb");
    assert_eq!(blocks.len(), 3);
    assert!(matches!(blocks[0], Block::Paragraph(_)));
    assert!(matches!(blocks[1], Block::Rule));
    assert!(matches!(blocks[2], Block::Paragraph(_)));
}

#[test]
fn rules_accept_all_three_marker_forms() {
    for src in ["---", "***", "___"] {
        let blocks = parse(src);
        assert_eq!(blocks.len(), 1, "{src:?}");
        assert!(matches!(blocks[0], Block::Rule), "{src:?}");
    }
}

#[test]
fn rules_render_as_a_blank_padded_thematic_row() {
    let lines = render("a\n\n---\n\nb", 12);
    let rendered: Vec<String> = lines
        .iter()
        .map(|line| line.spans.iter().map(|s| s.content.as_ref()).collect())
        .collect();
    assert_eq!(
        rendered,
        vec![
            "".to_string(),
            "  a".to_string(),
            "".to_string(),
            "  ────────".to_string(),
            "".to_string(),
            "  b".to_string(),
        ]
    );
    assert_eq!(lines[3].spans[1].style.fg, Some(theme::md_rule()));
}

#[test]
fn rules_lift_out_of_quotes_like_fences() {
    let blocks = parse("> ---");
    assert!(matches!(blocks[0], Block::Rule));
}

#[test]
fn degenerate_widths_render_no_rule_cells() {
    let lines = render("---", 2);
    assert!(!lines
        .iter()
        .any(|line| line.spans.iter().any(|s| s.content.contains('─'))));
}

#[test]
fn task_list_markers_stay_literal_item_text() {
    let blocks = parse("- [ ] todo\n- [x] done\n- [X] upper");
    let Block::List { start, items } = &blocks[0] else {
        panic!("expected a list");
    };
    assert_eq!(*start, None);
    assert_eq!(items.len(), 3);
    assert_eq!(text(&items[0].inline), "[ ] todo");
    assert_eq!(text(&items[1].inline), "[x] done");
    assert_eq!(text(&items[2].inline), "[X] upper");
}

#[test]
fn task_list_marker_appears_once_per_item() {
    let blocks = parse("- [x] done");
    let Block::List { items, .. } = &blocks[0] else {
        panic!("expected a list");
    };
    assert_eq!(items[0].inline.iter().filter(|sc| sc.0 == 'x').count(), 1);
    assert_eq!(text(&items[0].inline).matches("[x]").count(), 1);
}

#[test]
fn alt_text_after_an_inner_link_keeps_the_image_link() {
    let blocks = parse("![see [x](inner) more](img.png)");
    let Block::Paragraph(inline) = &blocks[0] else {
        panic!("expected a paragraph");
    };
    assert_eq!(text(inline), "🖼  see x more");
    let by_link = |id: Option<usize>| -> String {
        inline
            .iter()
            .filter(|sc| sc.2 == id)
            .map(|sc| sc.0)
            .collect()
    };
    assert_eq!(by_link(Some(0)), "🖼  see  more");
    assert_eq!(by_link(Some(1)), "x");
    assert_eq!(by_link(None), "");
}

#[test]
fn text_after_an_autolink_inside_a_link_keeps_the_outer_link() {
    let blocks = parse("[see https://x.io tail](page)");
    let Block::Paragraph(inline) = &blocks[0] else {
        panic!("expected a paragraph");
    };
    assert_eq!(text(inline), "see https://x.io tail");
    let linked: String = inline
        .iter()
        .filter(|sc| sc.2.is_some())
        .map(|sc| sc.0)
        .collect();
    assert_eq!(linked, "see https://x.io tail");
}

#[test]
fn an_image_inside_an_image_keeps_the_outer_target() {
    let blocks = parse("![a ![b](i2.png)](i1.png)");
    let Block::Paragraph(inline) = &blocks[0] else {
        panic!("expected a paragraph");
    };
    assert_eq!(text(inline), "🖼  a 🖼  b");
    let ids: Vec<Option<usize>> = inline.iter().map(|sc| sc.2).collect();
    assert!(ids.iter().all(|id| *id == Some(0)));
}

#[test]
fn images_render_as_an_emoji_prefixed_linked_alt() {
    let blocks = parse("![alt text](https://example.com/i.png) tail");
    let Block::Paragraph(inline) = &blocks[0] else {
        panic!("expected a paragraph");
    };
    assert_eq!(text(inline), "🖼  alt text tail");
    let linked: String = inline
        .iter()
        .filter(|sc| sc.2.is_some())
        .map(|sc| sc.0)
        .collect();
    assert_eq!(linked, "🖼  alt text");
    let underlined: String = inline
        .iter()
        .filter(|sc| sc.1.add_modifier.contains(Modifier::UNDERLINED))
        .map(|sc| sc.0)
        .collect();
    assert_eq!(underlined, "🖼  alt text");
}

#[test]
fn hard_breaks_split_the_rendered_row_soft_breaks_do_not() {
    let Block::Paragraph(inline) = &parse("line one,  \nline two.")[0] else {
        panic!("expected a paragraph");
    };
    assert_eq!(text(inline), "line one,\nline two.");
    let rows: Vec<String> = render("line one,  \nline two.", 40)
        .iter()
        .map(|line| line.spans.iter().map(|s| s.content.as_ref()).collect())
        .collect();
    assert_eq!(
        rows,
        vec![
            "".to_string(),
            "  line one,".to_string(),
            "  line two.".to_string()
        ]
    );

    let Block::Paragraph(soft) = &parse("line one\nline two.")[0] else {
        panic!("expected a paragraph");
    };
    assert_eq!(text(soft), "line one line two.");
    assert_eq!(render("line one\nline two.", 40).len(), 2);
}

#[test]
fn backslash_hard_breaks_also_split_the_row() {
    let rows: Vec<String> = render("line one\\\nline two.", 40)
        .iter()
        .map(|line| line.spans.iter().map(|s| s.content.as_ref()).collect())
        .collect();
    assert_eq!(
        rows,
        vec![
            "".to_string(),
            "  line one".to_string(),
            "  line two.".to_string()
        ]
    );
}

#[test]
fn emphasis_and_strong_style_their_text() {
    let Block::Paragraph(inline) = &parse("plain *italic* **bold**")[0] else {
        panic!("expected a paragraph");
    };
    assert_eq!(text(inline), "plain italic bold");
    let italic: String = inline
        .iter()
        .filter(|sc| sc.1.add_modifier.contains(Modifier::ITALIC))
        .map(|sc| sc.0)
        .collect();
    assert_eq!(italic, "italic");
    let bold: String = inline
        .iter()
        .filter(|sc| sc.1.add_modifier.contains(Modifier::BOLD))
        .map(|sc| sc.0)
        .collect();
    assert_eq!(bold, "bold");
}

#[test]
fn inline_code_keeps_its_text_and_gains_the_code_style() {
    let Block::Paragraph(inline) = &parse("run `cargo test` now")[0] else {
        panic!("expected a paragraph");
    };
    assert_eq!(text(inline), "run cargo test now");
    let code: String = inline
        .iter()
        .filter(|sc| sc.1.fg == Some(theme::md_code_inline()))
        .map(|sc| sc.0)
        .collect();
    assert_eq!(code, "cargo test");
}

#[test]
fn consecutive_hard_breaks_render_a_blank_row() {
    let rows: Vec<String> = render("one\\\n\\\ntwo", 40)
        .iter()
        .map(|line| line.spans.iter().map(|s| s.content.as_ref()).collect())
        .collect();
    assert_eq!(
        rows,
        vec![
            "".to_string(),
            "  one".to_string(),
            "  ".to_string(),
            "  two".to_string(),
        ]
    );
}

#[test]
fn hard_breaks_split_inside_quotes_and_list_items() {
    let quote: Vec<String> = render("> one,  \ntwo", 40)
        .iter()
        .map(|line| line.spans.iter().map(|s| s.content.as_ref()).collect())
        .collect();
    assert_eq!(
        quote,
        vec![
            "".to_string(),
            "".to_string(),
            "  ▌ one,".to_string(),
            "  ▌ two".to_string(),
        ]
    );

    let item: Vec<String> = render("- one,  \ntwo", 40)
        .iter()
        .map(|line| line.spans.iter().map(|s| s.content.as_ref()).collect())
        .collect();
    assert_eq!(
        item,
        vec![
            "".to_string(),
            "  • one,".to_string(),
            "    two".to_string()
        ]
    );
}

#[test]
fn raw_html_is_not_rendered() {
    let blocks = parse("<div>ignored</div>\n\nsay <b>bold</b> inline");
    let Block::Paragraph(inline) = &blocks[0] else {
        panic!("expected a paragraph");
    };
    assert_eq!(text(inline), "say bold inline");
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
fn strikethrough_crosses_out_the_text() {
    let blocks = parse("~~gone~~ kept");
    let Block::Paragraph(inline) = &blocks[0] else {
        panic!("expected a paragraph");
    };
    assert!(inline[0].1.add_modifier.contains(Modifier::CROSSED_OUT));
    assert!(!inline
        .last()
        .unwrap()
        .1
        .add_modifier
        .contains(Modifier::CROSSED_OUT));
}

#[test]
fn task_list_items_keep_their_checkbox_text() {
    let blocks = parse("- [ ] open\n- [x] done");
    let Block::List { start: None, items } = &blocks[0] else {
        panic!("expected a list");
    };
    assert_eq!(text(&items[0].inline), "[ ] open");
    assert_eq!(text(&items[1].inline), "[x] done");
}

#[test]
fn images_render_the_glyph_pair_and_alt_as_a_link_label() {
    let blocks = parse("![alt text](https://example.com/img.png)");
    let Block::Paragraph(inline) = &blocks[0] else {
        panic!("expected a paragraph");
    };
    assert_eq!(text(inline), "\u{1f5bc}  alt text");
    assert!(inline[0].1.add_modifier.contains(Modifier::UNDERLINED));
    assert_eq!(inline[0].2, Some(0), "the image registers a link target");
}
