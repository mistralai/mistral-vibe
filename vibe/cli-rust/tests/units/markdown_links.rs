//! Link runs stay on the labels they were rendered for, through composition, wrapping, and hover.

use ratatui::buffer::Buffer;
use ratatui::layout::Rect;
use ratatui::style::{Color, Style};
use ratatui::text::{Line, Span};
use vibe_rs::ui::markdown::{
    guttered, screen_links, Link, LinkKind, LinkedLines, MarkdownCache, Sc,
};
use vibe_rs::ui::theme;

fn prepare(text: &str, width: u16) -> LinkedLines {
    let prepared = MarkdownCache::default().prepare(0, 0, width, 0, || text.to_owned());
    prepared.linked().clone()
}

fn rect(width: u16, height: u16) -> Rect {
    Rect {
        x: 0,
        y: 0,
        width,
        height,
    }
}

fn links(lines: &LinkedLines) -> Vec<Link> {
    screen_links(lines, true, rect(200, 200), 0)
}

/// The text painted under a link's rows, row after row.
fn painted(lines: &LinkedLines, link: &Link) -> String {
    link.rows()
        .iter()
        .map(|&(y, x, width)| {
            let row: String = lines.lines()[y as usize]
                .spans
                .iter()
                .map(|span| span.content.as_ref())
                .collect();
            row.chars()
                .skip(x as usize)
                .take(width as usize)
                .collect::<String>()
        })
        .collect()
}

#[test]
fn inline_code_inside_a_label_belongs_to_the_link() {
    let lines = prepare(
        "See [Official Tokio `select!` documentation](<https://docs.rs/select>).",
        80,
    );
    let links = links(&lines);
    assert_eq!(links.len(), 1);
    assert_eq!(links[0].target(), "https://docs.rs/select");
    assert_eq!(links[0].rows(), [(1, 6, 36)]);
    assert_eq!(
        painted(&lines, &links[0]),
        "Official Tokio select! documentation"
    );
}

#[test]
fn a_wrapped_label_covers_each_row_it_lands_on() {
    let lines = prepare("aaaa [bb cc dd](https://x.io)", 14);
    let links = links(&lines);
    assert_eq!(links[0].rows(), [(1, 7, 5), (2, 2, 2)]);
    assert_eq!(painted(&lines, &links[0]), "bb ccdd");
}

#[test]
fn links_keep_document_order_and_autolinks_get_their_own_target() {
    let lines = prepare("bare https://example.com plus [`flag`](https://c.io)", 80);
    let links = links(&lines);
    let targets: Vec<_> = links.iter().map(Link::target).collect();
    assert_eq!(targets, ["https://example.com", "https://c.io"]);
    assert_eq!(painted(&lines, &links[1]), "flag");
}

#[test]
fn a_link_with_an_empty_label_has_no_area() {
    let lines = prepare("[](https://empty) then [two\nlines](https://split)", 80);
    let links = links(&lines);
    assert_eq!(links.len(), 1);
    assert_eq!(painted(&lines, &links[0]), "two lines");
}

#[test]
fn guttered_links_follow_dropped_blank_rows_and_the_gutter() {
    let lines = guttered("# Title\n\nSee [docs](https://d.io)", 40, Color::Reset);
    let links = links(&lines);
    assert_eq!(links[0].rows(), [(1, 6, 4)]);
    assert_eq!(painted(&lines, &links[0]), "docs");
}

#[test]
fn composed_lines_keep_each_link_on_its_label() {
    let mut lines = LinkedLines::from(vec![Line::from("header")]);
    lines.append(prepare("[one](https://1.io)", 40));
    lines.append(guttered("# T\n\n[two](https://2.io)", 40, Color::Reset));
    lines.retain(|index, _| index != 0);
    lines.prefix(|index| Span::raw(if index == 0 { "  ⎣ " } else { "  ⎢ " }));
    let links = links(&lines);
    let targets: Vec<_> = links.iter().map(Link::target).collect();
    assert_eq!(targets, ["https://1.io", "https://2.io"]);
    assert_eq!(painted(&lines, &links[0]), "one");
    assert_eq!(painted(&lines, &links[1]), "two");
}

#[test]
fn hovering_a_wrapped_link_highlights_every_row() {
    let lines = prepare("aaaa [bb cc dd](https://x.io)", 14);
    let link = &links(&lines)[0];
    let mut buffer = Buffer::empty(rect(14, 3));
    link.paint_hover(&mut buffer);
    let hover = theme::link_hover_style().bg;
    for &(y, x, width) in link.rows() {
        for x in x..x + width {
            assert_eq!(Some(buffer[(x, y)].bg), hover, "cell ({x}, {y})");
        }
    }
}

#[test]
fn a_line_paragraph_wraps_gets_no_hit_area_and_later_lines_shift_down() {
    let mut lines = prepare("[aaaa bbbb cccc](https://wide.io)", 40);
    lines.append(prepare("[ok](https://ok.io)", 40));
    let links = screen_links(&lines, false, rect(10, 10), 0);
    let targets: Vec<_> = links.iter().map(Link::target).collect();
    assert_eq!(targets, ["https://ok.io"]);
    assert_eq!(links[0].rows(), [(4, 2, 2)]);
}

#[test]
fn a_run_past_the_u16_column_range_is_dropped_not_wrapped_around() {
    let mut lines = LinkedLines::default();
    let link = lines.link("https://far.io".to_owned(), LinkKind::External);
    let label: Vec<Sc> = "far"
        .chars()
        .map(|c| (c, Style::default(), Some(link)))
        .collect();
    lines.push_row(
        vec![Span::raw(" ".repeat(usize::from(u16::MAX) + 2))],
        &label,
    );
    assert!(screen_links(&lines, true, rect(10, 5), 0).is_empty());
}

#[test]
fn image_alt_is_a_clickable_external_link_to_the_image() {
    let lines = prepare("![alt text](https://example.com/i.png) tail", 80);
    let links = links(&lines);
    assert_eq!(links.len(), 1);
    assert_eq!(links[0].target(), "https://example.com/i.png");
    assert_eq!(links[0].kind(), LinkKind::External);
    assert_eq!(painted(&lines, &links[0]), "🖼  alt text");
}

#[test]
fn an_image_inside_a_link_keeps_the_outer_target() {
    let lines = prepare("[![a](i.png)](page)", 80);
    let links = links(&lines);
    assert_eq!(links.len(), 1);
    assert_eq!(links[0].target(), "page");
    assert_eq!(painted(&lines, &links[0]), "🖼  a");
}

#[test]
fn text_after_a_nested_image_keeps_the_outer_link() {
    let lines = prepare("[![a](i.png) suffix](page)", 80);
    let links = links(&lines);
    assert_eq!(links.len(), 1);
    assert_eq!(links[0].target(), "page");
    assert_eq!(painted(&lines, &links[0]), "🖼  a suffix");
}

#[test]
fn an_empty_image_alt_is_just_the_linked_emoji_prefix() {
    let lines = prepare("![](u.png)", 80);
    let links = links(&lines);
    assert_eq!(links.len(), 1);
    assert_eq!(links[0].target(), "u.png");
    // Wrapping trims the row's trailing spaces; the emoji stays clickable.
    assert_eq!(painted(&lines, &links[0]), "🖼");
}

#[test]
fn a_link_split_by_a_hard_break_paints_on_both_rows() {
    let lines = prepare("[one,  \ntwo](https://x.io)", 40);
    let links = links(&lines);
    assert_eq!(links.len(), 1);
    assert_eq!(links[0].target(), "https://x.io");
    assert_eq!(links[0].rows(), [(1, 2, 4), (2, 2, 3)]);
}
