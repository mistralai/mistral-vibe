//! Long pastes: collapse criterion, atomic placeholder, expansion on re-paste and submit.

use std::sync::Arc;

use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};
use vibe_rs::app::App;
use vibe_rs::input::handle_paste;
use vibe_rs::input_modes::InputMode;
use vibe_rs::long_paste::{is_long, placeholder, HINT, MAX_INLINE_CHARS, MAX_INLINE_LINES};

fn lines(count: usize) -> String {
    (0..count)
        .map(|number| format!("line {number}"))
        .collect::<Vec<_>>()
        .join("\n")
}

#[test]
fn only_long_or_tall_pastes_collapse() {
    assert!(!is_long(&"a".repeat(MAX_INLINE_CHARS)));
    assert!(is_long(&"a".repeat(MAX_INLINE_CHARS + 1)));
    assert!(!is_long(&lines(MAX_INLINE_LINES)));
    assert!(is_long(&lines(MAX_INLINE_LINES + 1)));
    assert_eq!(placeholder("héllo"), "[Pasted 5 characters]");
}

#[test]
fn a_long_paste_collapses_into_an_atomic_placeholder_with_a_hint() {
    let mut app = App::default();
    app.chat_input.input = "see".into();
    app.chat_input.cursor = 3;
    let text = lines(30);

    handle_paste(&mut app, text.clone());

    let label = placeholder(&text);
    assert_eq!(app.chat_input.input, format!("see{label}"));
    let spans = app.chat_input.mentions.spans(&app.chat_input.input);
    assert_eq!(spans.len(), 1);
    assert_eq!((spans[0].start, spans[0].end), (3, 3 + label.len()));
    assert_eq!(spans[0].paste.as_deref(), Some(text.as_str()));
    assert_eq!(
        app.overlays
            .notice
            .as_ref()
            .map(|notice| notice.text.as_str()),
        Some(HINT)
    );
}

#[test]
fn pasting_the_same_text_right_away_shows_it_in_full() {
    let mut app = App::default();
    let text = lines(30);
    handle_paste(&mut app, text.clone());

    handle_paste(&mut app, text.clone());

    assert_eq!(app.chat_input.input, text);
    assert_eq!(app.chat_input.cursor, text.len());
    assert!(app.chat_input.expandable_paste.is_none());
    assert!(app.overlays.notice.is_none());
    assert!(app.chat_input.restore_edit(false));
    assert_eq!(app.chat_input.input, placeholder(&text));
}

#[test]
fn typing_ends_the_offer_so_the_same_paste_collapses_again() {
    let mut app = App::default();
    let client = Arc::new(vibe_rs::server::Client::stub());
    let (config_tx, _config_rx) = tokio::sync::mpsc::channel(1);
    let text = lines(30);
    handle_paste(&mut app, text.clone());

    let typed = KeyEvent::new(KeyCode::Char('x'), KeyModifiers::NONE);
    vibe_rs::input::handle_key(&mut app, &client, &config_tx, typed);

    assert!(app.overlays.notice.is_none());
    assert!(app.chat_input.expandable_paste.is_none());

    handle_paste(&mut app, text.clone());

    let label = placeholder(&text);
    assert_eq!(app.chat_input.input, format!("{label}x{label}"));
    assert_eq!(
        app.chat_input.mentions.spans(&app.chat_input.input).len(),
        2
    );
    assert_eq!(app.chat_input.submitted_text(), format!("{text}x{text}"));
}

#[test]
fn another_paste_ends_the_offer() {
    let mut app = App::default();
    let text = lines(30);
    handle_paste(&mut app, text.clone());
    handle_paste(&mut app, "short".into());

    handle_paste(&mut app, text.clone());

    let label = placeholder(&text);
    assert_eq!(app.chat_input.input, format!("{label}short{label}"));
}

#[test]
fn submitting_sends_the_pasted_text() {
    let mut app = App::default();
    app.chat_input.mode = InputMode::Bash;
    let text = lines(12);
    handle_paste(&mut app, text.clone());

    assert_eq!(app.chat_input.submitted_text(), format!("!{text}"));
}

#[test]
fn a_rewound_prompt_gets_its_collapsed_pastes_back() {
    let paste = format!("{}\n", lines(12));
    let sent = format!("summarize {}", lines(12));
    let mut pastes = vibe_rs::collapsed_pastes::CollapsedPastes::default();
    pastes.remember(paste.as_str().into());
    let display = pastes.display(&sent);
    let mut app = App::default();
    app.chat_input.load_full_text(sent.clone());

    vibe_rs::long_paste::restore(&mut app, display.as_ref());

    let label = placeholder(&paste);
    assert_eq!(app.chat_input.input, format!("summarize {label}"));
    let spans = app.chat_input.mentions.spans(&app.chat_input.input);
    assert_eq!(spans.len(), 1);
    assert_eq!(spans[0].start, "summarize ".len());
    assert_eq!(app.chat_input.submitted_text(), sent);
}

#[test]
fn a_rewound_slash_prompt_keeps_its_prefix_out_of_the_offsets() {
    let paste = lines(12);
    let sent = format!("/code-review {paste}");
    let mut pastes = vibe_rs::collapsed_pastes::CollapsedPastes::default();
    pastes.remember(paste.as_str().into());
    let display = pastes.display(&sent);
    let mut app = App::default();
    app.chat_input.load_full_text(sent.clone());

    vibe_rs::long_paste::restore(&mut app, display.as_ref());

    assert_eq!(app.chat_input.mode, InputMode::Slash);
    assert_eq!(
        app.chat_input.input,
        format!("code-review {}", placeholder(&paste))
    );
    assert_eq!(app.chat_input.submitted_text(), sent);
}

#[test]
fn the_same_paste_sent_three_times_stays_three_placeholders() {
    let mut app = App::default();
    let client = Arc::new(vibe_rs::server::Client::stub());
    let (config_tx, _config_rx) = tokio::sync::mpsc::channel(1);
    let newline = KeyEvent::new(KeyCode::Char('j'), KeyModifiers::CONTROL);
    let text = format!("{}\n", lines(12));
    for index in 0..3 {
        if index > 0 {
            vibe_rs::input::handle_key(&mut app, &client, &config_tx, newline);
        }
        handle_paste(&mut app, text.clone());
    }

    let sent = app.chat_input.submitted_text().trim().to_owned();
    let display = app.chat_input.collapsed_pastes.display(&sent);

    let label = placeholder(&text);
    assert_eq!(
        vibe_rs::collapsed_pastes::collapse(&sent, display.as_ref()),
        format!("{label}\n{label}\n{label}")
    );
}
