//! Ctrl+X and the rail's jump letters: entering, switching windows and hiding the letters.

use crossterm::event::KeyCode;
use vibe_rs::focus::Focus;
use vibe_rs::rail::{self, Item};

use crate::rail::with_items;
use crate::support::{ctrl, press, send_key, stub, type_text};

#[test]
fn ctrl_x_toggles_the_rail_from_anywhere_in_the_prompt() {
    let mut app = with_items(false);
    type_text(&mut app, "ab");
    press(&mut app, KeyCode::Left);
    press(&mut app, KeyCode::Right);
    press(&mut app, KeyCode::Right);
    assert_eq!(
        app.focus(),
        Focus::Composer,
        "Right never leaves the prompt"
    );

    press(&mut app, KeyCode::Left);
    send_key(&mut app, ctrl('x'));
    assert_eq!(app.focus(), Focus::Rail);
    assert_eq!(rail::selected(&app), Some(Item::Goal));

    send_key(&mut app, ctrl('x'));
    assert!(!app.rail.letters, "Ctrl+X again hides the letters");
    assert_eq!(app.focus(), Focus::Rail, "and stays on the rail");
    press(&mut app, KeyCode::Esc);
    assert_eq!(app.focus(), Focus::Composer);
    assert_eq!(app.chat_input.input, "ab");
}

#[test]
fn a_letter_opens_its_item_and_hides_the_letters() {
    let mut app = with_items(true);
    rail::enter(&mut app);
    press(&mut app, KeyCode::Char('g'));
    assert_eq!(rail::window(&app), Some(Item::Goal));
    assert!(!app.rail.letters);
    assert!(
        app.chat_input.input.is_empty(),
        "the letter never reaches the prompt"
    );

    rail::enter(&mut app);
    press(&mut app, KeyCode::Esc);
    press(&mut app, KeyCode::Esc);
    rail::enter(&mut app);
    press(&mut app, KeyCode::Char('x'));
    assert_eq!(app.focus(), Focus::Composer);
    assert_eq!(
        app.chat_input.input, "x",
        "with no window, other keys reach the prompt"
    );
}

#[test]
fn ctrl_x_then_a_letter_switches_the_open_window() {
    let mut app = with_items(true);
    rail::open(&mut app, &stub(), Item::Plan);
    send_key(&mut app, ctrl('x'));
    assert_eq!(app.focus(), Focus::Rail);
    assert!(app.rail.letters);
    assert_eq!(
        rail::window(&app),
        Some(Item::Plan),
        "the window stays under the letters"
    );

    press(&mut app, KeyCode::Char('g'));
    assert_eq!(rail::window(&app), Some(Item::Goal));
    assert_eq!(app.focus(), Focus::RailWindow);

    send_key(&mut app, ctrl('x'));
    press(&mut app, KeyCode::Char('x'));
    assert_eq!(app.focus(), Focus::RailWindow);
    assert!(
        app.chat_input.input.is_empty(),
        "no stray key reaches the prompt"
    );
}

#[test]
fn ctrl_x_over_the_subagent_list_opens_another_item() {
    let mut app = with_items(true);
    rail::open(&mut app, &stub(), Item::Agents);
    assert_eq!(app.focus(), Focus::SubagentList);
    send_key(&mut app, ctrl('x'));
    assert_eq!(app.focus(), Focus::Rail);

    press(&mut app, KeyCode::Char('t'));
    assert!(!app.subagents.list.focused, "the list closes");
    assert_eq!(rail::window(&app), Some(Item::Plan));
}

#[test]
fn letters_show_only_from_ctrl_x_until_an_item_opens() {
    let mut app = with_items(true);
    send_key(&mut app, ctrl('x'));
    assert!(app.rail.letters, "Ctrl+X from the prompt letters the items");

    press(&mut app, KeyCode::Char('t'));
    press(&mut app, KeyCode::Esc);
    assert_eq!(app.focus(), Focus::Rail);
    assert!(
        !app.rail.letters,
        "back on the rail from a window: no letters"
    );
}
