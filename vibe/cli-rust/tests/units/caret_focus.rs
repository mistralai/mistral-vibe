//! Regression: the caret kept blinking while the terminal window was unfocused.

use vibe_rs::app::App;

#[test]
fn losing_focus_darkens_the_caret_and_stops_the_blink() {
    let mut app = App::default();
    app.view.cursor_on = true;

    app.set_app_focus(false);

    assert!(!app.view.cursor_on);
    assert!(!app.view.app_focus);
}

#[test]
fn regaining_focus_relights_the_caret() {
    let mut app = App::default();
    app.set_app_focus(false);

    app.set_app_focus(true);

    assert!(app.view.cursor_on);
    assert!(app.view.app_focus);
}
