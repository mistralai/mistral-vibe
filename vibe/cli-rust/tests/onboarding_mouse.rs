//! Onboarding mouse interactions: click-to-select theme rows, link hit-testing.

use crossterm::event::{KeyModifiers, MouseButton, MouseEvent, MouseEventKind};
use ratatui::layout::Rect;
use vibe_rs::app::App;
use vibe_rs::mouse::MouseTarget;
use vibe_rs::setup::wizard::mouse::handle_mouse;
use vibe_rs::setup::wizard::{InputCard, OnboardingInput, OnboardingState, Screen};

fn click(col: u16, row: u16) -> MouseEvent {
    MouseEvent {
        kind: MouseEventKind::Down(MouseButton::Left),
        column: col,
        row,
        modifiers: KeyModifiers::NONE,
    }
}

#[test]
fn clicking_a_theme_row_selects_that_theme() {
    let mut app = App::default();
    let mut wizard = OnboardingState {
        theme_rows: vec![(Rect::new(45, 4, 30, 1), 2), (Rect::new(45, 8, 30, 1), 7)],
        ..OnboardingState::default()
    };
    handle_mouse(
        &mut app,
        &mut wizard,
        click(60, 8),
        MouseTarget::OnboardingThemeList,
    );
    assert_eq!(wizard.theme_index, 7);
}

#[test]
fn clicking_between_theme_rows_selects_nothing() {
    let mut app = App::default();
    let mut wizard = OnboardingState {
        theme_rows: vec![(Rect::new(45, 4, 30, 1), 2)],
        ..OnboardingState::default()
    };
    let before = wizard.theme_index;
    handle_mouse(
        &mut app,
        &mut wizard,
        click(60, 6),
        MouseTarget::OnboardingThemeList,
    );
    assert_eq!(wizard.theme_index, before);
}

#[test]
fn clicking_an_input_card_focuses_it_and_places_the_caret() {
    let mut app = App::default();
    let mut wizard = OnboardingState {
        screen: Screen::CustomDomain,
        custom_domain: OnboardingInput {
            value: "my.example".into(),
            ..OnboardingInput::default()
        },
        custom_domain_api: OnboardingInput {
            value: "https://api.my.example".into(),
            cursor: 22,
            ..OnboardingInput::default()
        },
        custom_domain_focus: 1,
        custom_domain_feedback: 1,
        input_rows: vec![
            (Rect::new(22, 20, 76, 3), InputCard::Domain),
            (Rect::new(22, 24, 76, 3), InputCard::DomainApi),
        ],
        ..OnboardingState::default()
    };
    // Click inside the domain card's value row, 3 columns in.
    handle_mouse(
        &mut app,
        &mut wizard,
        click(25, 21),
        MouseTarget::OnboardingInputs,
    );
    assert_eq!(wizard.custom_domain_focus, 0);
    assert_eq!(wizard.custom_domain.cursor, 0);
    // The feedback row is change-driven: focus never repaints it.
    assert_eq!(wizard.custom_domain_feedback, 1);

    // Clicking past the value clamps the caret to the end.
    handle_mouse(
        &mut app,
        &mut wizard,
        click(90, 25),
        MouseTarget::OnboardingInputs,
    );
    assert_eq!(wizard.custom_domain_focus, 1);
    assert_eq!(wizard.custom_domain_api.cursor, 22);
}

#[test]
fn clicking_the_api_key_card_moves_its_caret() {
    let mut app = App::default();
    let mut wizard = OnboardingState {
        screen: Screen::ApiKey,
        api_key_input: OnboardingInput {
            value: "test-key".into(),
            cursor: 8,
            ..OnboardingInput::default()
        },
        input_rows: vec![(Rect::new(22, 20, 76, 3), InputCard::ApiKey)],
        ..OnboardingState::default()
    };
    handle_mouse(
        &mut app,
        &mut wizard,
        click(29, 21),
        MouseTarget::OnboardingInputs,
    );
    assert_eq!(wizard.api_key_input.cursor, 4);
}

#[test]
fn clicking_outside_the_input_cards_does_nothing() {
    let mut app = App::default();
    let mut wizard = OnboardingState {
        input_rows: vec![(Rect::new(22, 20, 76, 3), InputCard::ApiKey)],
        ..OnboardingState::default()
    };
    handle_mouse(
        &mut app,
        &mut wizard,
        click(10, 21),
        MouseTarget::OnboardingInputs,
    );
    assert_eq!(wizard.api_key_input.cursor, 0);
}
