//! Spacing rows keep their line index through `append` and `retain`.

use ratatui::text::Line;

use vibe_rs::ui::markdown::{LinkedLines, MarkdownCache};

fn lines(gap_first: bool) -> LinkedLines {
    let mut lines = LinkedLines::default();
    if gap_first {
        lines.push_gap();
    }
    lines.push(Line::from("text"));
    lines.push(Line::from(""));
    lines
}

#[test]
fn push_gap_records_only_spacing_rows() {
    assert_eq!(lines(true).gaps(), &[0]);
    assert!(lines(false).gaps().is_empty());
}

#[test]
fn append_offsets_the_appended_gaps() {
    let mut all = lines(true);
    all.append(lines(true));
    assert_eq!(all.gaps(), &[0, 3]);
}

#[test]
fn retain_renumbers_the_kept_gaps() {
    let mut all = lines(true);
    all.append(lines(true));
    all.retain(|index, _| index != 1);
    assert_eq!(all.gaps(), &[0, 2]);
    all.retain(|index, _| index != 0);
    assert_eq!(all.gaps(), &[1]);
}

#[test]
fn assistant_markdown_marks_only_its_opening_gap() {
    let blank = |line: &Line<'static>| line.spans.iter().all(|span| span.content.trim().is_empty());
    let prepared = MarkdownCache::default().prepare(0, 0, 40, 0, || "one\n\ntwo".into());
    let linked = prepared.linked();
    let opening = linked.lines().iter().take_while(|line| blank(line)).count();
    assert!(opening > 0);
    assert_eq!(linked.gaps(), (0..opening).collect::<Vec<_>>());
    assert!(linked.lines()[opening..].iter().any(blank));
}
