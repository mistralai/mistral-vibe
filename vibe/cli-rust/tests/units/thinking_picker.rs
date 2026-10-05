//! Thinking picker refreshes: a changed set re-anchors, an unchanged set does not.

use serde_json::{json, Value};
use vibe_rs::app::App;
use vibe_rs::thinking_picker;

fn runtime(levels: &[&str]) -> Value {
    json!({"runtime": {"config": {"activeModel": {
        "thinking": "medium", "thinkingLevels": levels
    }}}})
}

#[test]
fn unchanged_set_keeps_the_moved_selection() {
    let mut app = App::default();
    thinking_picker::apply_runtime(&mut app, &runtime(&["off", "medium", "high"]));
    thinking_picker::open(&mut app);
    app.thinking_picker.selected = 0;
    thinking_picker::apply_runtime(&mut app, &runtime(&["off", "medium", "high"]));
    assert_eq!(app.thinking_picker.selected, 0);
}

#[test]
fn changed_set_re_anchors_within_bounds() {
    let mut app = App::default();
    thinking_picker::apply_runtime(&mut app, &runtime(&["off", "medium", "high"]));
    thinking_picker::open(&mut app);
    app.thinking_picker.selected = 2;
    thinking_picker::apply_runtime(&mut app, &runtime(&["off", "high"]));
    assert_eq!(app.thinking_picker.selected, 0);
    assert_eq!(app.thinking_picker.levels, vec!["off", "high"]);
}
