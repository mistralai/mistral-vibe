//! The preview's transcripts: read after the highlight rests with the rows
//! around it, kept for the last few sessions, and painted at once on open.

use crossterm::event::{KeyCode, KeyEvent};
use serde_json::json;
use tokio::sync::mpsc;
use vibe_rs::app::{App, Status};
use vibe_rs::server::PublicSessionState;
use vibe_rs::sessions_page::actions::{self, Event};
use vibe_rs::sessions_page::row::Session;
use vibe_rs::sessions_page::{self, keys, preview, Trigger};
use vibe_rs::ui_event::UiEvent;

use crate::support::stub;

fn session(id: &str, activity: u64) -> Session {
    Session {
        id: id.into(),
        title: id.into(),
        cwd: None,
        created_at: activity,
        updated_at: activity,
        bumped_at: Some(activity),
        ..Default::default()
    }
}

fn state(id: &str, text: &str) -> PublicSessionState {
    serde_json::from_value(json!({
        "eventId": 1,
        "session": { "id": id },
        "history": [{
            "id": format!("{id}-reply"),
            "type": "message",
            "role": "assistant",
            "generationStatus": "completed",
            "content": [{ "type": "text", "text": text }],
        }],
    }))
    .expect("state")
}

fn page(count: u64) -> (App, mpsc::Receiver<UiEvent>) {
    let mut app = App::default();
    app.set_status(Status::Ready);
    let (tx, rx) = mpsc::channel(64);
    app.events = Some(tx);
    sessions_page::open(&mut app, &stub(), Trigger::Left);
    app.sessions_page.loading = false;
    app.sessions_page.sessions = (0..count)
        .map(|index| session(&format!("s{index}"), 100 - index))
        .collect();
    sessions_page::relayout(&mut app);
    (app, rx)
}

fn cached(app: &App) -> Vec<String> {
    app.sessions_page
        .preview
        .sessions
        .iter()
        .map(|cached| cached.id.clone())
        .collect()
}

fn press(app: &mut App, code: KeyCode) {
    keys::handle_key(app, &stub(), KeyEvent::from(code));
}

fn screen(app: &mut App) -> String {
    crate::support::rows(&crate::support::render(120, 24, |frame| app.draw(frame))).join("\n")
}

/// The first rest reads its row alone; later ones read one row further the way the highlight moved.
#[tokio::test]
async fn a_rested_highlight_reads_its_row_and_the_next_one_the_way_it_moved() {
    let (mut app, _rx) = page(6);
    press(&mut app, KeyCode::Down);
    press(&mut app, KeyCode::Down);
    assert!(
        app.sessions_page.preview.at.is_some(),
        "the read waits for the highlight to rest"
    );
    sessions_page::read_preview(&mut app, &stub());
    assert!(app.sessions_page.preview.at.is_none());
    assert_eq!(app.sessions_page.preview.inflight(), ["s2"]);
    press(&mut app, KeyCode::Down);
    sessions_page::read_preview(&mut app, &stub());
    assert_eq!(app.sessions_page.preview.inflight(), ["s2", "s3", "s4"]);
    press(&mut app, KeyCode::Up);
    press(&mut app, KeyCode::Up);
    sessions_page::read_preview(&mut app, &stub());
    assert_eq!(
        app.sessions_page.preview.inflight(),
        ["s0", "s1", "s2", "s3", "s4"]
    );
}

/// A failed read says so instead of loading forever, and the next rest reads again.
#[tokio::test]
async fn a_failed_read_says_so_and_is_read_again() {
    let (mut app, _rx) = page(2);
    press(&mut app, KeyCode::Down);
    preview::store(&mut app, "s1".into(), 99, None);
    let text = screen(&mut app);
    assert!(text.contains("Couldn't load the conversation"), "{text}");
    assert!(!text.contains("Loading conversation"), "{text}");
    sessions_page::read_preview(&mut app, &stub());
    assert_eq!(app.sessions_page.preview.inflight(), ["s1"]);
}

/// A row read ahead waits at the least recent end: with the cache full, it
/// pushes out the least recently shown session rather than itself, so opening
/// it paints at once.
#[tokio::test]
async fn a_prefetch_into_a_full_cache_evicts_the_least_recently_shown_session() {
    let (mut app, _rx) = page(1);
    for index in 0..preview::CACHED {
        let id = format!("seen{index}");
        preview::store(&mut app, id.clone(), 1, Some(&state(&id, "Hi")));
        app.sessions_page.preview.touch(&id);
    }
    preview::store(&mut app, "ahead".into(), 1, Some(&state("ahead", "Hi")));
    let kept = cached(&app);
    assert_eq!(kept.len(), preview::CACHED);
    assert_eq!(kept.first().map(String::as_str), Some("ahead"));
    assert!(!kept.contains(&"seen0".to_owned()), "{kept:?}");
    assert!(app.sessions_page.preview.snapshot("ahead").is_some());
}

/// The current session shows its live conversation, not a read from before.
#[tokio::test]
async fn the_current_session_previews_its_live_conversation() {
    let (mut app, _rx) = page(1);
    app.set_session_id("s0".into());
    sessions_page::relayout(&mut app);
    app.view
        .transcript
        .load_snapshot(&state("s0", "Live reply right now"));
    preview::store(
        &mut app,
        "s0".into(),
        100,
        Some(&state("s0", "Stale reply")),
    );
    let text = screen(&mut app);
    assert!(text.contains("Live reply right now"), "{text}");
    assert!(!text.contains("Stale reply"), "{text}");
    sessions_page::read_preview(&mut app, &stub());
    assert!(
        app.sessions_page.preview.inflight().is_empty(),
        "the current session is not read"
    );
}

#[tokio::test]
async fn a_cached_transcript_is_not_read_again_until_the_session_changes() {
    let (mut app, _rx) = page(1);
    preview::store(&mut app, "s0".into(), 100, Some(&state("s0", "Hello")));
    sessions_page::read_preview(&mut app, &stub());
    assert!(app.sessions_page.preview.inflight().is_empty());
    app.sessions_page.sessions[0].updated_at = 200;
    sessions_page::read_preview(&mut app, &stub());
    assert_eq!(app.sessions_page.preview.inflight(), ["s0"]);
}

/// Past `CACHED`, the least recently shown session goes; showing one again keeps it.
#[tokio::test]
async fn the_cache_keeps_the_most_recently_shown_sessions() {
    let (mut app, _rx) = page(1);
    for index in 0..preview::CACHED {
        let id = format!("x{index}");
        preview::store(&mut app, id.clone(), 1, Some(&state(&id, "Hi")));
        app.sessions_page.preview.touch(&id);
    }
    app.sessions_page.preview.touch("x0");
    // The highlighted session's read lands: it is the most recent one.
    preview::store(&mut app, "s0".into(), 100, Some(&state("s0", "Hi")));
    let kept = cached(&app);
    assert_eq!(kept.len(), preview::CACHED);
    assert_eq!(kept.last().map(String::as_str), Some("s0"));
    assert!(
        kept.contains(&"x0".to_owned()),
        "x0 was shown again: {kept:?}"
    );
    assert!(!kept.contains(&"x1".to_owned()), "{kept:?}");
}

#[tokio::test]
async fn opening_a_previewed_session_paints_its_history_while_it_resumes() {
    let (mut app, _rx) = page(2);
    preview::store(
        &mut app,
        "s1".into(),
        99,
        Some(&state("s1", "Previewed reply")),
    );
    keys::handle_key(&mut app, &stub(), KeyEvent::from(KeyCode::Down));
    keys::handle_key(&mut app, &stub(), KeyEvent::from(KeyCode::Enter));
    assert!(sessions_page::switching(&app));
    assert_eq!(
        app.view.transcript.last_assistant_message().as_deref(),
        Some("Previewed reply")
    );
}

/// A failed open puts the conversation back where it was scrolled, and a
/// newer open drops the cached paint of the older one.
#[tokio::test]
async fn a_failed_open_restores_the_conversation_and_its_scroll() {
    let (mut app, _rx) = page(3);
    app.view
        .transcript
        .load_snapshot(&state("here", "The conversation left behind"));
    app.view.scroll = 7;
    app.view.scroll_target = 7;
    preview::store(
        &mut app,
        "s1".into(),
        99,
        Some(&state("s1", "Previewed reply")),
    );
    press(&mut app, KeyCode::Down);
    press(&mut app, KeyCode::Enter);
    let ticket = app.sessions_page.ticket;
    let failed = Event::Opened(ticket, Err("Session busy".into()));
    actions::apply_event(&mut app, &stub(), failed);
    assert!(!sessions_page::switching(&app));
    assert_eq!(
        app.view.transcript.last_assistant_message().as_deref(),
        Some("The conversation left behind")
    );
    assert_eq!((app.view.scroll, app.view.scroll_target), (7, 7));
}

fn long_state(id: &str, rows: usize) -> PublicSessionState {
    let text = (0..rows)
        .map(|row| format!("Line {row}"))
        .collect::<Vec<_>>()
        .join("\n\n");
    state(id, &text)
}

fn render_at(app: &mut App, width: u16) {
    crate::support::render(width, 24, |frame| app.draw(frame));
}

/// A preview column dropped by a narrow terminal leaves no area for clicks or the wheel.
#[tokio::test]
async fn a_hidden_preview_keeps_no_painted_area() {
    let (mut app, _rx) = page(1);
    preview::store(&mut app, "s0".into(), 100, Some(&long_state("s0", 40)));
    render_at(&mut app, 120);
    assert!(
        app.sessions_page.preview.view.area.width > 0,
        "the preview painted"
    );
    render_at(&mut app, 60);
    assert_eq!(
        app.sessions_page.preview.view.area,
        ratatui::layout::Rect::default()
    );
    assert!(app.sessions_page.preview.view.entries.is_empty());
}

/// The rename modal owns the wheel: the preview behind it does not scroll.
#[tokio::test]
async fn the_wheel_does_nothing_behind_the_rename_modal() {
    let (mut app, _rx) = page(1);
    preview::store(&mut app, "s0".into(), 100, Some(&long_state("s0", 40)));
    render_at(&mut app, 120);
    let area = app.sessions_page.preview.view.area;
    sessions_page::rename::start(&mut app);
    let over = crossterm::event::MouseEvent {
        kind: crossterm::event::MouseEventKind::ScrollUp,
        column: area.x + 1,
        row: area.y + 1,
        modifiers: crossterm::event::KeyModifiers::NONE,
    };
    sessions_page::mouse::wheel(&mut app, over, false);
    assert_eq!(app.sessions_page.preview.view.scroll, 0);
}

/// A fold that changed the conversation's height keeps the rows above the end in place.
#[tokio::test]
async fn a_folded_entry_keeps_its_rows_in_place() {
    let (mut app, _rx) = page(1);
    preview::store(&mut app, "s0".into(), 100, Some(&long_state("s0", 40)));
    render_at(&mut app, 120);
    let view = &mut app.sessions_page.preview.view;
    view.scroll = 5;
    // As a click that opened a tool call 7 rows tall would leave it.
    view.total -= 7;
    view.pin = true;
    render_at(&mut app, 120);
    assert_eq!(app.sessions_page.preview.view.scroll, 12);
    assert!(!app.sessions_page.preview.view.pin);
}

/// A click that clears a selection folds only on an entry's header row, as in the conversation.
#[tokio::test]
async fn a_click_clearing_a_selection_folds_only_on_a_header() {
    let (mut app, _rx) = page(1);
    // Two skill calls group: the group folds, and opened it spans rows below its header.
    let skill = |id: &str| {
        json!({
            "id": id,
            "type": "effect",
            "detail": { "kind": "skill", "toolName": "skill" },
            "state": { "status": "completed", "outputText": "private body" },
        })
    };
    let state = serde_json::from_value(json!({
        "eventId": 1,
        "session": { "id": "s0" },
        "history": [skill("skill-1"), skill("skill-2")],
    }))
    .expect("state");
    preview::store(&mut app, "s0".into(), 100, Some(&state));
    render_at(&mut app, 120);
    let opened = app.sessions_page.preview.view.entries[0].0;
    assert!(sessions_page::preview_view::toggle_at(&mut app, opened));
    render_at(&mut app, 120);
    // Then the first call in it, whose body shows under its header.
    let call = app.sessions_page.preview.view.entries[1].0;
    assert!(sessions_page::preview_view::toggle_at(&mut app, call));
    render_at(&mut app, 120);
    let entries = app.sessions_page.preview.view.entries.clone();
    let (body, header_row) = entries
        .iter()
        .find_map(|(top, bottom, header, _)| {
            let header = (*header)?;
            (*top..*bottom)
                .find(|row| *row != header)
                .map(|row| (row, header))
        })
        .unwrap_or_else(|| panic!("{entries:?}"));
    app.selection.had_selection_at_press = true;
    assert!(!sessions_page::preview_view::toggle_at(&mut app, body));
    assert!(sessions_page::preview_view::toggle_at(&mut app, header_row));
}

/// The preview's selection is anchored to the screen: the wheel scrolling the
/// conversation under it clears it rather than leaving it on other text.
#[tokio::test]
async fn the_wheel_over_the_preview_clears_its_selection() {
    let (mut app, _rx) = page(1);
    preview::store(&mut app, "s0".into(), 100, Some(&long_state("s0", 40)));
    render_at(&mut app, 120);
    let area = app.sessions_page.preview.view.area;
    app.selection.region = Some(vibe_rs::app::Selection {
        owner: vibe_rs::selection::RegionId::Preview,
        anchor: (1, 0),
        head: (2, 3),
        pending_copy: false,
        edge_scroll: 0,
        scroll_target: Default::default(),
        table_cell: None,
        text: String::new(),
    });
    let over = crossterm::event::MouseEvent {
        kind: crossterm::event::MouseEventKind::ScrollUp,
        column: area.x + 1,
        row: area.y + 1,
        modifiers: crossterm::event::KeyModifiers::NONE,
    };
    sessions_page::mouse::wheel(&mut app, over, false);
    assert!(
        app.sessions_page.preview.view.scroll > 0,
        "the preview scrolled"
    );
    assert!(app.selection.region.is_none());
}

/// A read the highlight was settling on is dropped when the page closes or
/// opens a session: the session's resume reads its history, and nothing shows
/// a read made after Esc.
#[tokio::test]
async fn closing_or_opening_drops_the_settling_preview_read() {
    let (mut app, _rx) = page(3);
    press(&mut app, KeyCode::Down);
    assert!(app.sessions_page.preview.at.is_some());
    press(&mut app, KeyCode::Enter);
    assert!(sessions_page::switching(&app));
    assert!(app.sessions_page.preview.at.is_none());
    sessions_page::read_preview(&mut app, &stub());
    assert!(app.sessions_page.preview.inflight().is_empty());

    let (mut app, _rx) = page(3);
    press(&mut app, KeyCode::Down);
    sessions_page::close(&mut app);
    assert!(app.sessions_page.preview.at.is_none());
    sessions_page::read_preview(&mut app, &stub());
    assert!(app.sessions_page.preview.inflight().is_empty());
}
