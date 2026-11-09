//! Quit confirmation state, and Ctrl+C clearing a draft before it arms quitting.

use vibe_rs::app::App;
use vibe_rs::input::handle_priority_key;
use vibe_rs::quit_manager::{quit_warning_extra, QuitConfirmKey, QuitManager};

use crate::support::{ctrl, stub};

fn ctrl_c(app: &mut App) -> Option<bool> {
    let event = ctrl('c');
    handle_priority_key(app, &stub(), event)
}

#[test]
fn nothing_is_confirmed_until_a_key_arms_it() {
    let mut quit = QuitManager::default();
    assert!(quit.active().is_none());
    assert!(!quit.is_confirmed(QuitConfirmKey::CtrlC));

    quit.request_confirmation(QuitConfirmKey::CtrlC, 0);

    assert!(quit.is_confirmed(QuitConfirmKey::CtrlC));
    assert!(!quit.is_confirmed(QuitConfirmKey::CtrlD));
}

#[test]
fn arming_the_other_key_replaces_the_pending_confirmation() {
    let mut quit = QuitManager::default();
    quit.request_confirmation(QuitConfirmKey::CtrlC, 0);

    quit.request_confirmation(QuitConfirmKey::CtrlD, 2);

    assert!(!quit.is_confirmed(QuitConfirmKey::CtrlC));
    assert!(quit.is_confirmed(QuitConfirmKey::CtrlD));
    assert_eq!(
        quit.active().unwrap().extra,
        "2 queued messages will be discarded"
    );
}

#[test]
fn cancelling_clears_the_confirmation_and_is_idempotent() {
    let mut quit = QuitManager::default();
    quit.cancel_confirmation();
    quit.request_confirmation(QuitConfirmKey::CtrlC, 1);

    quit.cancel_confirmation();

    assert!(quit.active().is_none());
    assert!(!quit.is_confirmed(QuitConfirmKey::CtrlC));
}

#[test]
fn the_queue_warning_counts_discarded_prompts() {
    assert_eq!(quit_warning_extra(0), "");
    assert_eq!(quit_warning_extra(1), "1 queued message will be discarded");
    assert_eq!(quit_warning_extra(3), "3 queued messages will be discarded");
}

#[test]
fn ctrl_c_clears_a_draft_before_it_arms_quitting() {
    let mut app = App::default();
    app.chat_input.input = "draft".into();
    app.chat_input.cursor = 5;

    assert_eq!(ctrl_c(&mut app), Some(false));
    assert_eq!(app.chat_input.input, "");
    assert!(app.quit.active().is_none(), "clearing does not arm quit");

    assert_eq!(ctrl_c(&mut app), Some(false));
    assert!(app.quit.is_confirmed(QuitConfirmKey::CtrlC));
    assert_eq!(ctrl_c(&mut app), Some(true));
}
