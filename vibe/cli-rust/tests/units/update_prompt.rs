//! The update prompt's state machine, labels, key mapping, and option text.

use crossterm::event::{KeyCode, KeyEvent, KeyEventKind, KeyModifiers};
use vibe_rs::update_notifier::gateway::UpdateSource;
use vibe_rs::update_prompt::{
    apply_key, option_span, UpdateChoice, UpdatePromptMode, UpdatePromptResult, UpdatePromptState,
};

fn dialog(mode: UpdatePromptMode) -> UpdatePromptState {
    UpdatePromptState::new("2.25.6", "2.99.0", mode, UpdateSource::Pypi)
}

fn key(code: KeyCode, modifiers: KeyModifiers) -> KeyEvent {
    KeyEvent::new(code, modifiers)
}

#[test]
fn default_selection_is_update_now() {
    let state = dialog(UpdatePromptMode::Startup);
    assert_eq!(state.selected, UpdateChoice::Update);
    assert_eq!(
        state.choices(),
        [UpdateChoice::Update, UpdateChoice::Continue]
    );
}

#[test]
fn labels_follow_the_prompt_mode() {
    assert_eq!(
        UpdatePromptMode::Startup.continue_label(),
        "Continue with current version"
    );
    assert_eq!(
        UpdatePromptMode::CheckUpgrade.continue_label(),
        "Cancel upgrade"
    );
    assert_eq!(
        UpdateChoice::Update.label(UpdatePromptMode::Startup),
        "Update now"
    );
    assert_eq!(
        UpdateChoice::Continue.label(UpdatePromptMode::CheckUpgrade),
        "Cancel upgrade"
    );
}

#[test]
fn navigation_swaps_and_wraps_between_the_two_choices() {
    let mut state = dialog(UpdatePromptMode::Startup);
    state.move_selection();
    assert_eq!(state.selected, UpdateChoice::Continue);
    // Two choices: the modulo cycle lands back on Update.
    state.move_selection();
    assert_eq!(state.selected, UpdateChoice::Update);
}

#[test]
fn navigation_is_ignored_while_updating() {
    let mut state = dialog(UpdatePromptMode::Startup);
    state.updating = true;
    state.move_selection();
    assert_eq!(state.selected, UpdateChoice::Update);
    assert_eq!(
        apply_key(&mut state, key(KeyCode::Left, KeyModifiers::empty())),
        None
    );
    assert_eq!(
        apply_key(&mut state, key(KeyCode::Enter, KeyModifiers::empty())),
        None
    );
}

#[test]
fn key_mapping_mirrors_the_python_bindings() {
    let mut state = dialog(UpdatePromptMode::Startup);
    // Left/right move.
    assert_eq!(
        apply_key(&mut state, key(KeyCode::Left, KeyModifiers::empty())),
        None
    );
    assert_eq!(state.selected, UpdateChoice::Continue);
    assert_eq!(
        apply_key(&mut state, key(KeyCode::Right, KeyModifiers::empty())),
        None
    );
    assert_eq!(state.selected, UpdateChoice::Update);
    // Enter on Continue answers Continue.
    apply_key(&mut state, key(KeyCode::Left, KeyModifiers::empty()));
    assert_eq!(
        apply_key(&mut state, key(KeyCode::Enter, KeyModifiers::empty())),
        Some(UpdatePromptResult::Continue)
    );
    // Enter on Update flips to the updating state without answering yet.
    let mut state = dialog(UpdatePromptMode::CheckUpgrade);
    assert_eq!(
        apply_key(&mut state, key(KeyCode::Enter, KeyModifiers::empty())),
        None
    );
    assert!(state.updating);
    // Ctrl+C and Ctrl+Q quit from either selection.
    for code in [KeyCode::Char('c'), KeyCode::Char('q')] {
        let mut state = dialog(UpdatePromptMode::Startup);
        assert_eq!(
            apply_key(&mut state, key(code, KeyModifiers::CONTROL)),
            Some(UpdatePromptResult::Quit)
        );
    }
    // Release-kind events (key repeat on some terminals) are ignored.
    let mut state = dialog(UpdatePromptMode::Startup);
    let mut release = key(KeyCode::Enter, KeyModifiers::empty());
    release.kind = KeyEventKind::Release;
    assert_eq!(apply_key(&mut state, release), None);
    assert!(!state.updating);
}

#[test]
fn option_span_marks_only_the_selected_choice() {
    let state = dialog(UpdatePromptMode::Startup);
    let update = option_span(UpdateChoice::Update, &state);
    let other = option_span(UpdateChoice::Continue, &state);
    assert_eq!(update.content, " Update now ");
    assert_eq!(other.content, " Continue with current version ");
    assert!(
        update.style.bg.is_some(),
        "the selected chip is a cursor bar"
    );
    assert!(other.style.bg.is_none());
}

#[test]
fn updating_state_draws_the_cat_one_row_per_grid_row() {
    use ratatui::backend::TestBackend;
    use ratatui::Terminal;
    use vibe_rs::ui::banner::petit_chat::PetitChat;

    let mut state = dialog(UpdatePromptMode::Startup);
    state.updating = true;
    let mut chat = PetitChat::default();
    let mut terminal = Terminal::new(TestBackend::new(80, 24)).unwrap();
    vibe_rs::update_prompt::draw(&mut terminal, &state, &mut chat);
    let buffer = terminal.backend().buffer();
    // Every braille-dotted row of the grid must land on its own terminal row;
    // a single `Line` would collapse them into one garbled row.
    let dotted_rows = (0..buffer.area.height)
        .filter(|&y| {
            (0..buffer.area.width).any(|x| {
                buffer[(x, y)]
                    .symbol()
                    .chars()
                    .any(|ch| ('\u{2800}'..='\u{28ff}').contains(&ch))
            })
        })
        .count();
    assert_eq!(dotted_rows, 3, "cat rows collapsed to {dotted_rows}");
}

#[test]
fn a_stopped_quit_key_reader_never_touches_the_terminal() {
    // The reader must be stoppable on its own: the current-thread runtime
    // joins every spawn_blocking task at drop, so a reader parked in a
    // blocking read would hang the process after the dialog is done.
    let stop = std::sync::atomic::AtomicBool::new(true);
    assert!(!vibe_rs::update_prompt::quit_key_reader(&stop));
}
