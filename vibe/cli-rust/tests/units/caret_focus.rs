//! Caret visibility follows terminal focus without sharing the main-input preference.

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

#[test]
fn main_input_blinks_by_default() {
    let mut app = App::default();
    for phase in [false, true] {
        app.view.cursor_on = phase;
        assert_eq!(app.main_input_cursor_on(), phase);
    }
}

#[test]
fn steady_main_input_does_not_change_the_modal_caret_phase() {
    let mut app = App::default();
    app.session.startup_config.cursor_blink = false;

    for phase in [false, true, false] {
        app.view.cursor_on = phase;
        assert!(app.main_input_cursor_on());
        assert_eq!(app.view.cursor_on, phase);
    }

    app.set_app_focus_from_terminal(false);
    assert!(!app.main_input_cursor_on());
    app.set_app_focus_from_terminal(true);
    assert!(app.main_input_cursor_on());
}
