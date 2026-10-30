//! Thinking picker refreshes: a changed set re-anchors, an unchanged set does not;
//! a level of the model's own is listed and picked as written.

use ratatui::backend::TestBackend;
use ratatui::Terminal;
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
    app.thinking_picker.list.highlight(0);
    thinking_picker::apply_runtime(&mut app, &runtime(&["off", "medium", "high"]));
    assert_eq!(app.thinking_picker.list.selected(), 0);
}

#[test]
fn changed_set_re_anchors_within_bounds() {
    let mut app = App::default();
    thinking_picker::apply_runtime(&mut app, &runtime(&["off", "medium", "high"]));
    thinking_picker::open(&mut app);
    app.thinking_picker.list.highlight(2);
    thinking_picker::apply_runtime(&mut app, &runtime(&["off", "high"]));
    assert_eq!(app.thinking_picker.list.selected(), 0);
    assert_eq!(app.thinking_picker.levels, vec!["off", "high"]);
}

#[test]
fn a_level_of_the_models_own_is_listed_and_picked_as_written() {
    let mut app = App::default();
    let runtime = json!({"runtime": {"config": {"activeModel": {
        "thinking": "turbo", "thinkingLevels": ["low", "turbo"]
    }}}});
    thinking_picker::apply_runtime(&mut app, &runtime);
    thinking_picker::open(&mut app);

    let mut terminal = Terminal::new(TestBackend::new(60, 20)).expect("terminal");
    terminal
        .draw(|frame| vibe_rs::ui::thinking_picker::draw(&mut app, frame, frame.area()))
        .expect("draw");
    let buffer = terminal.backend().buffer();
    let screen: String = (0..20)
        .map(|y| (0..60).map(|x| buffer[(x, y)].symbol()).collect::<String>())
        .collect::<Vec<_>>()
        .join("\n");

    assert!(screen.contains("Low"), "{screen}");
    assert!(screen.contains("Turbo"), "{screen}");
    assert_eq!(thinking_picker::selected_level(&app), "turbo");
}
