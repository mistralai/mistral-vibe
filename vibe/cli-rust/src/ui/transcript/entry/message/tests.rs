use ratatui::style::{Modifier, Style};

use super::push_command_result;
use crate::ui::markdown::LinkedLines;
use crate::ui::theme;

const TEXT: &str = "- **Tokens**: 1.23M _(1,234,567)_";

fn exact_style(muted_emphasis: bool) -> Style {
    let mut lines = LinkedLines::default();
    push_command_result(&mut lines, TEXT, 80, muted_emphasis);
    lines
        .lines()
        .iter()
        .flat_map(|line| &line.spans)
        .find(|span| span.content.contains("(1,234,567)"))
        .map(|span| span.style)
        .expect("the exact count renders")
}

#[test]
fn agent_statistics_render_emphasis_muted_instead_of_italic() {
    let style = exact_style(true);
    assert!(!style.add_modifier.contains(Modifier::ITALIC));
    assert_eq!(style.fg, Some(theme::muted()));
    assert_eq!(style.add_modifier.contains(Modifier::DIM), theme::is_ansi());
}

#[test]
fn command_results_keep_their_italics() {
    let style = exact_style(false);
    assert!(style.add_modifier.contains(Modifier::ITALIC));
    assert_ne!(style.fg, Some(theme::muted()));
}
