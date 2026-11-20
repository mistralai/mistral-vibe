use ratatui::style::Style;

use super::layout::{clip, lines};
use crate::mcp::rows::{Row, SourceRow};
use crate::server::MCPSourceKind;
use crate::ui::visual_lines::text;

fn plain(chars: &[crate::ui::visual_lines::Sc]) -> String {
    chars.iter().map(|(character, _)| character).collect()
}

#[test]
fn cutting_only_trailing_blanks_adds_no_ellipsis() {
    let row = text("a_long_tool_name      ", Style::default());
    assert_eq!(plain(&clip(row, 16)), "a_long_tool_name");
}

#[test]
fn cutting_visible_text_ends_on_an_ellipsis() {
    let row = text("a_long_tool_name  description", Style::default());
    assert_eq!(plain(&clip(row, 20)), "a_long_tool_name  d…");
    assert_eq!(plain(&clip(text("abcdef", Style::default()), 4)), "abc…");
}

#[test]
fn no_room_leaves_nothing_not_even_an_ellipsis() {
    assert_eq!(plain(&clip(text("abc", Style::default()), 0)), "");
}

#[test]
fn a_status_that_fills_the_row_stays_whole() {
    let status = "needs a personal API key";
    let row = Row::Source(SourceRow {
        name: "gmail".to_owned(),
        kind: MCPSourceKind::Connector,
        label: "gmail".to_owned(),
        transport: "[http]".to_owned(),
        owner: String::new(),
        tools: "1 tool".to_owned(),
        symbol: "○",
        connected: false,
        status: status.to_owned(),
        action: None,
        blocked: true,
    });
    // Two leading blanks, the name gap, `○ `: no room is left for the name itself.
    let width = 2 + 2 + 2 + status.len();
    let line = &lines(&[row], 1, width)[0];
    let shown: String = line
        .spans
        .iter()
        .map(|span| span.content.as_ref())
        .collect();
    assert!(shown.ends_with(status), "{shown:?}");
}
