//! Completion popup scroll reconciliation.

use vibe_rs::app::App;
use vibe_rs::completion_manager::{self, CompletionEntry};
use vibe_rs::ui::completion_popup;

#[test]
fn moving_up_past_the_widest_file_keeps_the_selection_in_view() {
    let mut app = App::default();
    app.completion.entries = std::iter::once("@a_longest_name.md".to_owned())
        .chain(('b'..='m').map(|letter| format!("@{letter}.md")))
        .map(|label| CompletionEntry {
            label,
            description: String::new(),
        })
        .collect();
    for _ in 0..11 {
        completion_manager::navigate(&mut app, false);
        completion_popup::reconcile_scroll(&mut app, 80);
    }
    assert_eq!(app.completion.selected, 2);
    assert_eq!(app.completion.scroll, 2, "the selection is the top row");
}
