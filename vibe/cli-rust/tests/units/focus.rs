//! One precedence order decides which surface owns keys, paste and the screen.

use vibe_rs::app::App;
use vibe_rs::focus::Focus;

#[test]
fn the_composer_owns_input_when_nothing_is_open() {
    assert_eq!(App::default().focus(), Focus::Composer);
}

#[test]
fn config_wins_over_a_blocking_bottom_app() {
    let mut app = App::default();
    app.approval.open = true;
    app.config_screen.open = true;

    assert_eq!(app.focus(), Focus::Config);
}

#[test]
fn a_server_callback_wins_over_a_user_opened_picker() {
    let mut app = App::default();
    app.theme_picker.open = true;
    app.approval.open = true;

    assert_eq!(app.focus(), Focus::Approval);
}

#[test]
fn any_bottom_app_wins_over_the_focused_subagent_list() {
    let mut app = App::default();
    app.subagents.list.focused = true;
    assert_eq!(app.focus(), Focus::SubagentList);

    app.question_app.open = true;
    assert_eq!(app.focus(), Focus::Question);
}

#[test]
fn the_trust_gate_wins_over_everything() {
    let mut app = App::default();
    app.config_screen.open = true;
    app.trust.open = true;

    assert_eq!(app.focus(), Focus::Trust);
}
