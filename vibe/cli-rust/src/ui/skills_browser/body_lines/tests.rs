use crate::skills_browser::labels;
use crate::skills_browser::model::Body;
use crate::ui::visual_lines::VisualLine;

use super::{body_lines, paragraph};

fn text(lines: &[VisualLine]) -> Vec<String> {
    lines
        .iter()
        .map(|line| {
            line.spans
                .iter()
                .map(|span| span.content.as_ref())
                .collect()
        })
        .collect()
}

#[test]
fn an_empty_body_says_so() {
    let lines = body_lines(&Body::Ready(String::new()), 60);
    assert_eq!(text(&lines), [format!("  {}", labels::EMPTY_BODY)]);
}

#[test]
fn a_body_on_its_way_says_it_is_loading() {
    let lines = body_lines(&Body::Loading, 60);
    assert_eq!(text(&lines), [format!("  {}", labels::LOADING_BODY)]);
}

#[test]
fn a_body_that_failed_says_why() {
    let lines = body_lines(&Body::Failed("timed out".to_owned()), 80);
    assert_eq!(
        text(&lines),
        [format!("  {}: timed out", labels::BODY_FAILED)]
    );
}

#[test]
fn tabs_expand_to_their_stops() {
    assert_eq!(
        text(&paragraph("a\tb\n\tc", 40)),
        ["  a       b", "          c"]
    );
}
