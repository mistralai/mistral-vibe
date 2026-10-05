//! Subagent status-list state transitions (Python `SubagentList` + app wiring).

use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};
use vibe_rs::app::App;
use vibe_rs::server::{PublicChildSession, SessionStatus};
use vibe_rs::subagents::{self, MAIN_SESSION_ID};

fn child(id: &str, status: SessionStatus) -> PublicChildSession {
    PublicChildSession {
        id: id.into(),
        name: format!("{id}-name"),
        agent_type: "explore".into(),
        status,
        created_at: 1,
        updated_at: 2,
        ..Default::default()
    }
}

fn running(id: &str) -> PublicChildSession {
    child(id, SessionStatus::Running)
}

fn key(code: KeyCode) -> KeyEvent {
    KeyEvent::new(code, KeyModifiers::NONE)
}

fn upsert(app: &mut App, session: PublicChildSession) {
    app.subagents.replace_child_session(session);
    subagents::refresh(app);
}

#[test]
fn refresh_lists_children_and_keeps_the_batch_held() {
    let mut app = App::default();
    upsert(&mut app, running("a"));
    upsert(&mut app, running("b"));
    assert_eq!(
        ids(&app.subagents.list.rows),
        ["a", "b"],
        "active children are listed"
    );
    // Both go idle inside the same batch: still listed (no flicker).
    upsert(&mut app, child("a", SessionStatus::Idle));
    upsert(&mut app, child("b", SessionStatus::Idle));
    assert_eq!(ids(&app.subagents.list.rows), ["a", "b"]);
}

#[test]
fn focusing_the_list_and_up_at_main_returns_to_the_input() {
    let mut app = App::default();
    upsert(&mut app, running("a"));
    assert!(subagents::focus_first(&mut app));
    assert!(app.subagents.list.focused);
    assert_eq!(app.subagents.list.highlighted, 0);
    // Up on the Main row hands focus back (nothing is viewed).
    assert!(subagents::handle_list_key(&mut app, key(KeyCode::Up)));
    assert!(!app.subagents.list.focused);
}

#[test]
fn enter_selects_the_highlighted_child_and_esc_returns_to_main() {
    let mut app = App::default();
    upsert(&mut app, running("a"));
    subagents::focus_first(&mut app);
    assert!(subagents::handle_list_key(&mut app, key(KeyCode::Down)));
    assert_eq!(app.subagents.list.highlighted, 1);
    assert!(subagents::handle_list_key(&mut app, key(KeyCode::Enter)));
    assert_eq!(app.subagents.viewed_subagent_id.as_deref(), Some("a"));
    assert!(
        app.subagents.list.focused,
        "the list keeps focus while viewing"
    );
    assert_eq!(app.subagents.list.selected_session_id.as_deref(), Some("a"));
    subagents::show_main_chat(&mut app, true);
    assert_eq!(app.subagents.viewed_subagent_id, None);
    assert!(!app.subagents.list.focused);
}

#[test]
fn enter_on_main_while_in_main_chat_is_a_noop() {
    let mut app = App::default();
    upsert(&mut app, running("a"));
    subagents::focus_first(&mut app);
    assert!(subagents::handle_list_key(&mut app, key(KeyCode::Enter)));
    assert_eq!(app.subagents.viewed_subagent_id, None);
    assert!(app.subagents.list.focused);
}

#[test]
fn the_viewed_child_vanishing_returns_to_main_chat() {
    let mut app = App::default();
    upsert(&mut app, running("a"));
    subagents::show_subagent_chat(&mut app, "a");
    assert_eq!(app.subagents.viewed_subagent_id.as_deref(), Some("a"));
    // The server drops the child entirely.
    app.subagents.sessions.clear();
    subagents::refresh(&mut app);
    assert_eq!(app.subagents.viewed_subagent_id, None);
}

#[test]
fn disabling_the_list_closes_an_active_view() {
    let mut app = App::default();
    upsert(&mut app, running("a"));
    subagents::show_subagent_chat(&mut app, "a");
    app.subagents.status_list_enabled = false;
    subagents::refresh(&mut app);
    assert_eq!(app.subagents.viewed_subagent_id, None);
    assert!(app.subagents.list.rows.is_empty());
}

#[test]
fn a_focused_list_hands_focus_back_when_it_empties() {
    let mut app = App::default();
    upsert(&mut app, running("a"));
    subagents::focus_first(&mut app);
    app.subagents.sessions.clear();
    subagents::refresh(&mut app);
    assert!(app.subagents.list.rows.is_empty());
    assert!(!app.subagents.list.focused);
}

#[test]
fn opening_a_child_view_moves_the_highlight_to_it() {
    let mut app = App::default();
    upsert(&mut app, running("a"));
    upsert(&mut app, running("b"));
    subagents::show_subagent_chat(&mut app, "b");
    assert_eq!(app.subagents.list.highlighted, 2);
    // Returning to main moves the highlight back to the Main row.
    subagents::show_main_chat(&mut app, false);
    assert_eq!(app.subagents.list.highlighted, 0);
    assert!(
        app.subagents.list.focused,
        "Main selection keeps the list focused"
    );
}

#[test]
fn selecting_a_missing_child_returns_to_main() {
    let mut app = App::default();
    upsert(&mut app, running("a"));
    subagents::select(&mut app, Some("missing".into()));
    assert_eq!(app.subagents.viewed_subagent_id, None);
}

#[test]
fn selecting_main_from_a_child_view_focuses_the_input() {
    let mut app = App::default();
    upsert(&mut app, running("a"));
    subagents::show_subagent_chat(&mut app, "a");
    assert!(app.subagents.list.focused);
    // Enter on the Main row behaves like Esc: leave the child view and give
    // the composer focus instead of parking the highlight on Main.
    assert!(subagents::handle_list_key(&mut app, key(KeyCode::Home)));
    assert!(subagents::handle_list_key(&mut app, key(KeyCode::Enter)));
    assert_eq!(app.subagents.viewed_subagent_id, None);
    assert!(!app.subagents.list.focused);
    assert!(app.view.app_focus);
}

#[test]
fn marker_follows_the_keyboard_highlight_while_focused() {
    let mut app = App::default();
    upsert(&mut app, running("a"));
    subagents::focus_first(&mut app);
    subagents::handle_list_key(&mut app, key(KeyCode::Down));
    assert_eq!(
        app.subagents
            .list
            .mouse_session_id
            .or_else(|| {
                app.subagents.list.focused.then(|| {
                    if app.subagents.list.highlighted == 0 {
                        MAIN_SESSION_ID.to_owned()
                    } else {
                        app.subagents.list.rows[app.subagents.list.highlighted - 1]
                            .id
                            .clone()
                    }
                })
            })
            .as_deref(),
        Some("a")
    );
}

#[test]
fn the_highlight_follows_its_row_across_batch_changes() {
    let mut app = App::default();
    upsert(&mut app, running("a"));
    upsert(&mut app, running("b"));
    upsert(&mut app, running("c"));
    subagents::focus_first(&mut app);
    subagents::handle_list_key(&mut app, key(KeyCode::Down));
    subagents::handle_list_key(&mut app, key(KeyCode::Down));
    subagents::handle_list_key(&mut app, key(KeyCode::Down));
    assert_eq!(app.subagents.list.highlighted, 3, "the last child");
    // `b` drops out of the batch; the highlight stays on `c` (Python re-indexes).
    app.subagents.sessions.retain(|session| session.id != "b");
    app.subagents.list.batch_session_ids.remove("b");
    subagents::refresh(&mut app);
    assert_eq!(app.subagents.list.highlighted, 2);
    assert_eq!(ids(&app.subagents.list.rows), ["a", "c"]);
}

#[test]
fn tokens_come_from_context_usage_only() {
    let mut session = running("a");
    session.token_usage.total_tokens = 500;
    assert_eq!(session.context_tokens(), 0);
    session.context_usage = Some(vibe_rs::server::TokenUsage {
        total_tokens: 2500,
        ..Default::default()
    });
    assert_eq!(session.context_tokens(), 2500);
}

#[test]
fn page_home_end_and_jk_navigate_like_the_option_list() {
    let mut app = App::default();
    for id in ["a", "b", "c", "d", "e", "f", "g"] {
        upsert(&mut app, running(id));
    }
    subagents::focus_first(&mut app);
    assert!(subagents::handle_list_key(&mut app, key(KeyCode::PageDown)));
    assert_eq!(
        app.subagents.list.highlighted, 7,
        "a page of 7 rows from Main lands on the last child"
    );
    assert!(subagents::handle_list_key(&mut app, key(KeyCode::PageUp)));
    assert_eq!(app.subagents.list.highlighted, 0);
    assert!(subagents::handle_list_key(
        &mut app,
        key(KeyCode::Char('j'))
    ));
    assert_eq!(app.subagents.list.highlighted, 1);
    assert!(subagents::handle_list_key(
        &mut app,
        key(KeyCode::Char('k'))
    ));
    assert_eq!(app.subagents.list.highlighted, 0);
    assert!(subagents::handle_list_key(&mut app, key(KeyCode::End)));
    assert_eq!(app.subagents.list.highlighted, 7);
    assert!(subagents::handle_list_key(&mut app, key(KeyCode::Home)));
    assert_eq!(app.subagents.list.highlighted, 0);
}

#[test]
fn the_wheel_scrolls_the_viewport_without_moving_the_highlight() {
    let mut app = App::default();
    for id in ["a", "b", "c", "d", "e", "f", "g", "h"] {
        upsert(&mut app, running(id));
    }
    subagents::focus_first(&mut app);
    vibe_rs::subagents::list::wheel(&mut app, true, 2);
    assert_eq!(app.subagents.list.free_scroll, Some(2));
    assert_eq!(app.subagents.list.highlighted, 0, "the highlight stays put");
    // A highlight move pins the viewport back to it.
    assert!(subagents::handle_list_key(&mut app, key(KeyCode::Down)));
    assert_eq!(app.subagents.list.free_scroll, None);
    // The wheel cannot scroll past the last row.
    vibe_rs::subagents::list::wheel(&mut app, true, 50);
    assert_eq!(app.subagents.list.free_scroll, Some(8));
}

#[test]
fn live_updates_keep_the_wheel_scrolled_viewport_until_the_highlight_moves() {
    let mut app = App::default();
    for id in ["a", "b", "c"] {
        upsert(&mut app, running(id));
    }
    subagents::focus_first(&mut app);
    vibe_rs::subagents::list::wheel(&mut app, true, 1);
    assert_eq!(app.subagents.list.free_scroll, Some(1));
    // A status or token tick that does not move the highlight keeps the
    // wheel-scrolled viewport in place.
    let mut session = running("b");
    session.token_usage.total_tokens = 40;
    upsert(&mut app, session);
    assert_eq!(
        app.subagents.list.free_scroll,
        Some(1),
        "a live tick without a highlight move keeps the viewport"
    );
    // A rebuilt row set resets the viewport (Python `clear_options`).
    app.subagents
        .seed_snapshot(vec![running("a"), running("b")]);
    subagents::refresh(&mut app);
    assert_eq!(
        app.subagents.list.free_scroll, None,
        "a row-set change re-pins the viewport without a highlight move"
    );
    // A highlight move pins the viewport back to it.
    assert!(subagents::handle_list_key(&mut app, key(KeyCode::Down)));
    assert_eq!(app.subagents.list.free_scroll, None);
}

#[test]
fn the_context_progress_follows_the_viewed_child() {
    let mut app = App::default();
    app.subagents.main_tokens = (100, 600);
    app.session.tokens = (100, 600);
    let mut session = running("a");
    session.context_usage = Some(vibe_rs::server::TokenUsage {
        total_tokens: 250,
        ..Default::default()
    });
    upsert(&mut app, session);
    subagents::show_subagent_chat(&mut app, "a");
    assert_eq!(app.session.tokens, (250, 600), "the child's context usage");
    // A stats update while viewing keeps the main budget for the return.
    app.subagents.main_tokens = (300, 600);
    assert_eq!(app.session.tokens, (250, 600), "still masked");
    subagents::show_main_chat(&mut app, true);
    assert_eq!(
        app.session.tokens,
        (300, 600),
        "restored from the latest stats"
    );
}

#[test]
fn resetting_the_views_anchors_the_main_conversation() {
    let mut app = App::default();
    upsert(&mut app, running("a"));
    subagents::show_subagent_chat(&mut app, "a");
    // The user wheel-scrolled the child transcript before the reset landed.
    app.view.scroll = 40;
    app.view.scroll_target = 40;
    subagents::reset_views(&mut app);
    assert_eq!(app.subagents.viewed_subagent_id, None);
    assert_eq!(
        (app.view.scroll, app.view.scroll_target),
        (0, 0),
        "the rebuilt main conversation starts anchored, not at the child's offset"
    );
}

fn ids(rows: &[PublicChildSession]) -> Vec<&str> {
    rows.iter().map(|row| row.id.as_str()).collect()
}

#[test]
fn killing_the_viewed_child_keeps_it_listed_until_the_view_closes() {
    let mut app = App::default();
    // The child runs and finishes while nobody views it, so the batch clears
    // and only the idle filter keeps it listed.
    upsert(&mut app, running("a"));
    upsert(&mut app, child("a", SessionStatus::Idle));
    subagents::show_subagent_chat(&mut app, "a");
    assert_eq!(ids(&app.subagents.list.rows), ["a"]);

    // Killed while viewed: the archived child is neither active nor idle, but
    // the read-only notice needs its row until the user returns to Main.
    upsert(&mut app, child("a", SessionStatus::Archived));
    assert_eq!(app.subagents.viewed_subagent_id.as_deref(), Some("a"));
    assert_eq!(ids(&app.subagents.list.rows), ["a"]);

    // Leaving the view drops the stopped child: it is outside every batch.
    subagents::show_main_chat(&mut app, true);
    subagents::refresh(&mut app);
    assert!(app.subagents.list.rows.is_empty());
}

#[tokio::test]
async fn submitting_a_recalled_prompt_leaves_the_next_down_free() {
    let mut app = App::default();
    app.chat_input.history.add("earlier prompt");
    upsert(&mut app, running("a"));

    let client = std::sync::Arc::new(vibe_rs::server::Client::stub());
    let (config_tx, _config_rx) = tokio::sync::mpsc::channel(1);

    // Recall the last prompt with Up, then submit it: the composer clears,
    // but the loaded-entry state must clear with it.
    vibe_rs::input::handle_key(&mut app, &client, &config_tx, key(KeyCode::Up));
    assert_eq!(app.chat_input.input, "earlier prompt");
    vibe_rs::input::handle_key(&mut app, &client, &config_tx, key(KeyCode::Enter));
    assert!(app.chat_input.input.is_empty());

    // The very next Down focuses the list instead of feeding a stale recall.
    vibe_rs::input::handle_key(&mut app, &client, &config_tx, key(KeyCode::Down));
    assert!(
        app.subagents.list.focused,
        "Down focuses the list after a submit"
    );
}

#[test]
fn a_paste_is_dropped_while_the_list_or_a_child_view_owns_focus() {
    let mut app = App::default();
    upsert(&mut app, running("a"));

    // While the list is focused, a paste never reaches the hidden composer.
    assert!(subagents::focus_first(&mut app));
    vibe_rs::input::handle_paste(&mut app, "pasted".into());
    assert_eq!(app.chat_input.input, "");

    // Same while a child transcript view is open.
    app.subagents.list.focused = false;
    app.subagents.viewed_subagent_id = Some("a".into());
    vibe_rs::input::handle_paste(&mut app, "pasted".into());
    assert_eq!(app.chat_input.input, "");

    // Back on the main conversation, the paste lands.
    app.subagents.viewed_subagent_id = None;
    vibe_rs::input::handle_paste(&mut app, "pasted".into());
    assert_eq!(app.chat_input.input, "pasted");
}
