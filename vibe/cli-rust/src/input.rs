//! Keyboard input handling for the prompt and completion popup.

use std::sync::atomic::{AtomicU32, Ordering};
use std::sync::Arc;

use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};
use tokio::sync::mpsc;

use crate::app::{App, Status};
use crate::commands::{stress, submission};
use crate::config_write::Scope;
use crate::focus::Focus;
use crate::mouse::{scroll_chat, KEY_SCROLL_STEP};
use crate::quit_manager::QuitConfirmKey;
use crate::server::Client;
use crate::utils::input_edit;
use crate::{
    agents, chat_input, clipboard, completion_manager, config, config_write, connector_auth,
    feedback, keymap, log_level_picker, mcp, mcp_oauth, message_queue, model_picker, paste_files,
    paste_image, paste_path, question_input, rewind, subagents, theme_picker, thinking_picker,
    turn_summary, voice,
};

fn is_ctrl_c(key: &KeyEvent) -> bool {
    // Ctrl+Shift+C is copy (handled below), so quit is Ctrl+C without shift.
    key.modifiers.contains(KeyModifiers::CONTROL)
        && !key.modifiers.contains(KeyModifiers::SHIFT)
        && key.code == KeyCode::Char('c')
}

/// Ctrl+R toggles voice recording (Python voice keybinding).
fn is_ctrl_r(key: &KeyEvent) -> bool {
    key.modifiers.contains(KeyModifiers::CONTROL) && key.code == KeyCode::Char('r')
}

fn is_ctrl_d(key: &KeyEvent) -> bool {
    key.modifiers.contains(KeyModifiers::CONTROL) && key.code == KeyCode::Char('d')
}

#[cfg(unix)]
pub fn request_suspend(app: &mut App, key: &KeyEvent) -> bool {
    let requested = key.modifiers.contains(KeyModifiers::CONTROL) && key.code == KeyCode::Char('z');
    app.suspend_requested |= requested;
    requested
}

#[cfg(not(unix))]
pub fn request_suspend(_: &mut App, _: &KeyEvent) -> bool {
    false
}

/// Copy: Ctrl+Y on Unix, Ctrl+Shift+C or Command+C everywhere.
fn is_copy_key(key: &KeyEvent) -> bool {
    let ctrl = key.modifiers.contains(KeyModifiers::CONTROL);
    let command = key.modifiers.contains(KeyModifiers::SUPER);
    let shift = key.modifiers.contains(KeyModifiers::SHIFT);
    (command && key.code == KeyCode::Char('c'))
        || (ctrl
            && ((cfg!(unix) && key.code == KeyCode::Char('y') && !shift)
                || (key.code == KeyCode::Char('c') && shift)))
}

/// The cached text of an active mouse selection (region or bottom bar),
/// extracted at paint time. Empty selections yield `None`. The surfaces are
/// mutually exclusive, so at most one is set.
fn selection_text(app: &App) -> Option<String> {
    let region = app.selection.region.as_ref().map(|sel| sel.text.clone());
    let bottom_bar = app
        .selection
        .bottom_bar
        .as_ref()
        .map(|sel| sel.text.clone());
    region.or(bottom_bar).filter(|text| !text.is_empty())
}

/// Handle a copy binding without letting the active screen consume it.
pub(crate) fn handle_copy_key(app: &mut App, key: &KeyEvent) -> bool {
    if !is_copy_key(key) {
        return false;
    }
    app.chat_input.normalize_positions();
    if !copy_selected_input(app) {
        if let Some(text) = selection_text(app) {
            // Region and bottom-bar text are cached at paint time so the copy
            // works regardless of the autocopy setting.
            clipboard::copy_to_clipboard(&text);
            crate::telemetry::user_copied_text(app, &text);
        }
    }
    true
}

/// Toggle the plan panel: Cmd+\, or the Alt+\ (ESC \) remap terminals deliver instead.
fn is_todo_key(key: &KeyEvent) -> bool {
    !key.modifiers.contains(KeyModifiers::CONTROL)
        && key
            .modifiers
            .intersects(KeyModifiers::SUPER | KeyModifiers::ALT)
        && key.code == KeyCode::Char('\\')
}

/// Handle application-level bindings before a modal consumes local keys.
pub fn handle_priority_key(app: &mut App, client: &Arc<Client>, key: KeyEvent) -> Option<bool> {
    if key.code == KeyCode::BackTab {
        if app.focus() == Focus::VibeCodeProject {
            return None;
        }
        agents::cycle(app, client);
        return Some(false);
    }
    // The crash notice says "Press Ctrl+C to exit": nothing is left to copy, cancel or confirm.
    if is_ctrl_c(&key) && app.server_closed {
        return Some(true);
    }
    if (is_ctrl_c(&key) || is_copy_key(&key))
        && crate::vibe_code_project::input::copy_selection(app, false)
    {
        return Some(false);
    }
    if is_ctrl_c(&key) && crate::proxy_setup::copy_selection(app, false) {
        return Some(false);
    }
    if is_ctrl_c(&key) {
        if app.recording_active() {
            app.cancel_recording();
            return Some(false);
        }
        if stress::stop(app) {
            return Some(false);
        }
        // Queue selection takes Ctrl+C to remove the highlighted prompt (ADR 0013).
        if app.queue.selected.is_some() && !app.queue.editing && composer_reachable(app) {
            message_queue::handle_selection_key(app, client, key);
            return Some(false);
        }
        app.chat_input.normalize_positions();
        if composer_owns_key(app) && copy_selected_input(app) {
            return Some(false);
        }
        return Some(handle_ctrl_c(app, client));
    }
    if is_ctrl_d(&key) {
        return Some(handle_ctrl_d(app));
    }
    if handle_copy_key(app, &key) {
        return Some(false);
    }
    if key.modifiers.contains(KeyModifiers::SHIFT) {
        match key.code {
            KeyCode::Up => scroll_chat(app, true, KEY_SCROLL_STEP),
            KeyCode::Down => scroll_chat(app, false, KEY_SCROLL_STEP),
            _ => return None,
        }
        return Some(false);
    }
    None
}

/// Cut the selection or current line: Ctrl+X (Textual TextArea `cut`).
fn is_cut_key(key: &KeyEvent) -> bool {
    key.modifiers.contains(KeyModifiers::CONTROL) && key.code == KeyCode::Char('x')
}

fn copy_selected_input(app: &App) -> bool {
    let input = &app.chat_input;
    let Some((lo, hi)) = chat_input::selection_range(&input.input, input.cursor, input.anchor)
    else {
        return false;
    };
    // A collapsed paste copies as the text it stands for; pasted back, it collapses again.
    let text = input.mentions.expanded_range(&input.input, lo, hi);
    clipboard::copy_to_clipboard(&text);
    crate::telemetry::user_copied_text(app, &text);
    true
}

fn composer_owns_key(app: &App) -> bool {
    app.focus() == Focus::Composer && (app.queue.selected.is_none() || app.queue.editing)
}

/// Whether the composer and its queue mode receive keys before any modal or view.
pub(crate) fn composer_reachable(app: &App) -> bool {
    app.focus() == Focus::Composer && app.subagents.viewed_subagent_id.is_none()
}

/// Handle one key press; true means the application should exit.
pub fn handle_key(
    app: &mut App,
    client: &Arc<Client>,
    config_tx: &mpsc::Sender<config::Loaded>,
    key: KeyEvent,
) -> bool {
    let before = if composer_owns_key(app) && !app.recording_active() {
        crate::composer_history::before_key(app, &key)
    } else {
        app.chat_input.edit_history.checkpoint();
        None
    };
    let exit = handle_key_inner(app, client, config_tx, key);
    crate::composer_history::finish(&mut app.chat_input, before);
    exit
}

fn handle_key_inner(
    app: &mut App,
    client: &Arc<Client>,
    config_tx: &mpsc::Sender<config::Loaded>,
    key: KeyEvent,
) -> bool {
    if let Some(exit) = handle_priority_key(app, client, key) {
        return exit;
    }
    app.chat_input.normalize_positions();
    // The subagent list owns navigation and selection while focused (Python
    // `SubagentList`); other keys fall through, and the unfocused composer
    // never edits (Python's TextArea ignores keys without focus).
    if app.subagents.list.focused {
        let ctrl = key.modifiers.contains(KeyModifiers::CONTROL);
        let plain = key.modifiers.is_empty();
        let list_key = matches!(
            key.code,
            KeyCode::Up
                | KeyCode::Down
                | KeyCode::Enter
                | KeyCode::Home
                | KeyCode::End
                | KeyCode::PageUp
                | KeyCode::PageDown
        ) || (plain && matches!(key.code, KeyCode::Char('j') | KeyCode::Char('k')));
        match key.code {
            _ if list_key => {
                subagents::handle_list_key(app, key);
                return false;
            }
            // App-level bindings still run while the list is focused.
            KeyCode::Esc => {}
            KeyCode::Char('o') if ctrl => {}
            _ => return false,
        }
    }
    // Reaching here the chat input owns the key (Textual `ChatTextArea._on_key`).
    app.chat_input.last_keystroke = Some(std::time::Instant::now());
    // Ctrl+R toggles voice recording (Python `_handle_voice_key`).
    if is_ctrl_r(&key) && app.voice.mode_enabled {
        app.toggle_recording();
        return false;
    }
    // While recording, Esc cancels and any other key stops it.
    if app.recording_active() {
        if key.code == KeyCode::Esc {
            app.cancel_recording();
        } else if app.voice.transcribe_state == voice::TranscribeState::Recording {
            app.stop_recording();
        }
        return false;
    }
    if is_todo_key(&key) {
        app.todo_sidebar.open = !app.todo_sidebar.open;
        return false;
    }
    if app.feedback.message == feedback::Message::Prompt
        && !key
            .modifiers
            .intersects(KeyModifiers::CONTROL | KeyModifiers::ALT | KeyModifiers::SUPER)
    {
        match key.code {
            KeyCode::Char(rating @ '1'..='3') => {
                feedback::rate(app, client, rating.to_digit(10).unwrap_or_default() as u8);
                return false;
            }
            KeyCode::Char('0') => {
                feedback::snooze(app, client);
                return false;
            }
            KeyCode::Char(_) => feedback::hide(app),
            _ => {}
        }
    }
    // Queue selection locks the input: navigation, removal, edit and escape are
    // intercepted before any editing key (ADR 0013), only where its hints show.
    if app.queue.selected.is_some() && !app.queue.editing && composer_reachable(app) {
        message_queue::handle_selection_key(app, client, key);
        return false;
    }
    // Editing a queued prompt: only Escape is special, the rest edits the text.
    if app.queue.editing && key.code == KeyCode::Esc && composer_reachable(app) {
        message_queue::end_edit(app);
        return false;
    }
    if crate::external_editor::request(app, &key) {
        return false;
    }
    // Cut the chat input selection or current line; copy is an app-level priority binding.
    if is_cut_key(&key) {
        app.chat_input.scroll = None;
        crate::long_paste::dismiss(app);
        // Without a selection a cut takes the caret line, from its start.
        let line_start = app.chat_input.line_cut_start();
        let text = app
            .chat_input
            .edit_atomically(line_start, |input, cursor, anchor| {
                chat_input::cut(input, cursor, anchor);
            });
        if !text.is_empty() {
            clipboard::copy_to_clipboard(&text);
            reset_history_state(app);
            completion_manager::input_changed(app);
        }
        return false;
    }
    if paste_image::is_paste_image_key(&key, paste_image::is_supported()) {
        paste_image::request(app, true);
        return false;
    }
    if app.chat_input.apply_mode_key(&key) {
        app.chat_input.scroll = None;
        crate::long_paste::dismiss(app);
        reset_history_state(app);
        completion_manager::input_changed(app);
        return false;
    }
    // ChatInput editing (keymap -> Action -> apply), decoupled from the backend.
    // An edit resets history recall and the loaded-entry state and refreshes
    // completion; a caret/selection move only marks the caret as moved.
    if let Some(action) = keymap::action_for(&key) {
        app.chat_input.scroll = None;
        if matches!(action, chat_input::Action::Undo | chat_input::Action::Redo) {
            crate::composer_history::restore(app, &action);
            return false;
        }
        let edit = action.is_edit();
        if edit {
            reset_history_state(app);
        }
        let from = app.chat_input.cursor;
        if edit {
            crate::long_paste::dismiss(app);
            // A line deletion starts at the start of the first line it takes.
            let line_start = matches!(action, chat_input::Action::DeleteLine).then(|| {
                let input = &app.chat_input;
                let first = input
                    .anchor
                    .map_or(input.cursor, |anchor| anchor.min(input.cursor));
                input_edit::line_bounds(&input.input, first).0
            });
            app.chat_input
                .edit_atomically(line_start, |input, cursor, anchor| {
                    chat_input::apply(&action, input, cursor, anchor);
                });
            // Raw-keystroke drops only get image mentions; mixed lists need a bracketed paste.
            crate::composer_paths::rewrite_image_paths(&mut app.chat_input);
            completion_manager::input_changed(app);
        } else {
            chat_input::apply(
                &action,
                &mut app.chat_input.input,
                &mut app.chat_input.cursor,
                &mut app.chat_input.anchor,
            );
            app.chat_input.snap_cursor(from);
            mark_cursor_moved(app);
            // Completion queries end at the caret, so a move re-filters the popup.
            completion_manager::refresh(app);
        }
        return false;
    }

    // App-level keys: Esc, scroll, completion/history nav, accept, submit.
    let shift = key.modifiers.contains(KeyModifiers::SHIFT);
    let ctrl = key.modifiers.contains(KeyModifiers::CONTROL);
    match key.code {
        // Python `_try_interrupt` checks the subagent view first.
        KeyCode::Esc if app.subagents.viewed_subagent_id.is_some() => {
            app.overlays.last_escape = None;
            subagents::show_main_chat(app, true);
        }
        KeyCode::Esc if completion_manager::dismiss(app) => app.overlays.last_escape = None,
        KeyCode::Esc if app.session.shell_operation_id.is_some() => {
            crate::commands::shell::interrupt(app, client);
            app.overlays.last_escape = Some(std::time::Instant::now());
        }
        KeyCode::Esc if crate::teleport::busy(app) => {
            crate::teleport::interrupt(app, client);
            app.overlays.last_escape = Some(std::time::Instant::now());
        }
        // Only a docked panel owns Esc; a dropped column must not swallow the interrupt.
        KeyCode::Esc if app.todo_sidebar.close() => {}
        // Escape while generating interrupts the turn (Python `_interrupt_turn`).
        KeyCode::Esc if matches!(app.session.status, Status::Generating { .. }) => {
            submission::interrupt_turn(app, client);
            app.overlays.last_escape = Some(std::time::Instant::now());
        }
        // An active narrator stops without arming the double-escape (Python `_try_interrupt_no_job_steps`).
        KeyCode::Esc if turn_summary::cancel(app) => {}
        KeyCode::Esc => handle_escape(app, client),
        // Ctrl+O toggles every tool group and result body (Python `toggle_tool`).
        KeyCode::Char('o') if ctrl => app.toggle_tools(),
        KeyCode::Up if shift => scroll_chat(app, true, KEY_SCROLL_STEP),
        KeyCode::Down if shift => scroll_chat(app, false, KEY_SCROLL_STEP),
        KeyCode::Up if completion_manager::is_open(app) => completion_manager::navigate(app, false),
        KeyCode::Down if completion_manager::is_open(app) => {
            completion_manager::navigate(app, true)
        }
        KeyCode::PageUp if key.modifiers.is_empty() => move_cursor_page(app, false),
        KeyCode::PageDown if key.modifiers.is_empty() => move_cursor_page(app, true),
        // Up/Down move the caret within a multi-line chat input; at the top/bottom
        // they recall input history (Textual's `_handle_history_{up,down}`).
        KeyCode::Up => {
            app.chat_input.scroll = None;
            let (target, moved_row) = crate::ui::chat_input::vertical_cursor(app, false);
            if !handle_history_up(app, !moved_row) {
                let from = app.chat_input.cursor;
                app.chat_input.cursor = target;
                app.chat_input.anchor = None;
                app.chat_input.snap_cursor(from);
                mark_cursor_moved(app);
                completion_manager::refresh(app);
            }
        }
        KeyCode::Down => {
            app.chat_input.scroll = None;
            let (target, moved_row) = crate::ui::chat_input::vertical_cursor(app, true);
            let history = handle_history_down(app, !moved_row);
            if !history {
                // Python `NavigateBelow`: focus the subagent list at the Main
                // row when the caret is on the last wrapped line.
                if !moved_row && subagents::focus_first(app) {
                    return false;
                }
                if !moved_row {
                    app.set_app_focus(true);
                }
                let from = app.chat_input.cursor;
                app.chat_input.cursor = target;
                app.chat_input.anchor = None;
                app.chat_input.snap_cursor(from);
                mark_cursor_moved(app);
                completion_manager::refresh(app);
            }
        }
        KeyCode::Tab => completion_manager::tab(app),
        // A file (`@`) or skill (`/`) mention completion accepts on Enter without submitting.
        KeyCode::Enter
            if completion_manager::active_is_mention(app) && completion_manager::accept(app) => {}
        // A slash completion accepts the highlighted entry (completing e.g.
        // `/them` to `/theme`) and runs it in the same Enter, like Python's
        // SlashCommandController returning SUBMIT. With no popup open, accept is
        // a no-op and the typed text is dispatched as-is.
        // An agent switch in flight swallows the submit (Python
        // `ChatInputBody.on_chat_text_area_submitted` returns while switching).
        KeyCode::Enter if agents::switching(app) => {}
        KeyCode::Enter => {
            if completion_manager::accept(app) {
                completion_manager::input_changed(app);
            }
            let exit = submission::submit(app, client, config_tx);
            // `submission` clears the input on submit but does not know about the
            // chat input caret; keep it in bounds so the next render is consistent.
            app.chat_input.cursor = app.chat_input.cursor.min(app.chat_input.input.len());
            app.chat_input.anchor = None;
            return exit;
        }
        _ => {}
    }
    false
}

/// Escape with nothing to interrupt: a second one inside `DOUBLE_ESC_DELAY`
/// clears a non-empty input, else it enters rewind mode (Python
/// `_handle_input_double_escape`).
fn handle_escape(app: &mut App, client: &Arc<Client>) {
    let now = std::time::Instant::now();
    let doubled = app
        .overlays
        .last_escape
        .is_some_and(|at| now.duration_since(at) < rewind::DOUBLE_ESC_DELAY);
    app.overlays.last_escape = if doubled { None } else { Some(now) };
    if !doubled {
        return;
    }
    if app.chat_input.full_text().is_empty() {
        rewind::start(app, client);
        return;
    }
    reset_history_state(app);
    app.chat_input.clear();
    completion_manager::input_changed(app);
}

fn move_cursor_page(app: &mut App, down: bool) {
    app.chat_input.scroll = None;
    app.chat_input.anchor = None;
    let from = app.chat_input.cursor;
    app.chat_input.cursor = crate::ui::chat_input::page_cursor(app, down);
    app.chat_input.snap_cursor(from);
    mark_cursor_moved(app);
    completion_manager::refresh(app);
}

/// The `/theme` picker owns keys: arrows/jk preview, Enter selects, Esc reverts.
pub fn handle_theme_key(app: &mut App, client: &Arc<Client>, key: KeyEvent) {
    match key.code {
        KeyCode::Esc => {
            theme_picker::cancel(app);
        }
        KeyCode::Up | KeyCode::Char('k') => theme_picker::navigate(app, false),
        KeyCode::Down | KeyCode::Char('j') => theme_picker::navigate(app, true),
        KeyCode::Enter => select_theme(app, client),
        _ => {}
    }
}

/// Commit the highlighted theme: apply it (a debounced preview may still be
/// pending), close the picker and persist it via `config/write`, mirroring
/// Python's `ThemeSelected`.
/// The written runtime comes back on the response; re-apply its theme so the UI
/// reflects the server's config exactly, as Python does.
fn select_theme(app: &mut App, client: &Arc<Client>) {
    let theme = theme_picker::selected_name(app).to_string();
    let Some(session_id) = app.session.session_id.clone() else {
        theme_picker::cancel(app);
        return;
    };
    theme_picker::preview(app);
    app.theme_picker.open = false;
    let client = client.clone();
    let theme_tx = app.theme_picker.tx.clone();
    let pending = app.commit_started();
    tokio::spawn(async move {
        let ops = vec![config_write::set_op("/theme", theme)];
        let result =
            config_write::write(&client, &session_id, ops, "app-server config update").await;
        let applied = result.ok().and_then(|result| {
            result
                .pointer("/runtime/config/theme")
                .and_then(|value| value.as_str())
                .map(str::to_owned)
        });
        let event = match applied {
            Some(name) => theme_picker::Event::Applied(name),
            None => theme_picker::Event::Failed,
        };
        deliver(theme_tx, event, &pending).await;
    });
}

/// The `/mcp` browser owns keys while open (Python `MCPApp.BINDINGS`).
/// The log-level picker owns keys while open. Esc applies rather than cancels,
/// matching Python's `Binding("escape", "apply")`.
pub fn handle_log_level_key(app: &mut App, client: &Arc<Client>, key: KeyEvent) {
    match key.code {
        KeyCode::Esc => log_level_picker::apply(app, client),
        KeyCode::Up | KeyCode::Char('k') => log_level_picker::navigate(app, false),
        KeyCode::Down | KeyCode::Char('j') => log_level_picker::navigate(app, true),
        KeyCode::Left | KeyCode::Char('h') => {
            log_level_picker::focus_badge(app, log_level_picker::BADGE_SESSION);
        }
        KeyCode::Right | KeyCode::Char('l') => {
            log_level_picker::focus_badge(app, log_level_picker::BADGE_CONFIG);
        }
        KeyCode::Enter => log_level_picker::toggle_badge(app),
        _ => {}
    }
}

pub fn handle_mcp_key(app: &mut App, client: &Arc<Client>, key: KeyEvent) {
    if mcp::search::handle_key(app, key) {
        return;
    }
    match key.code {
        // Esc backs out one level: the tool list to the source list, then closes.
        KeyCode::Esc if app.mcp.viewing_name.is_some() => mcp::back(app),
        KeyCode::Esc => mcp::close(app),
        KeyCode::Up | KeyCode::Char('k') => mcp::navigate(app, false),
        KeyCode::Down | KeyCode::Char('j') => mcp::navigate(app, true),
        KeyCode::Enter => mcp::select(app),
        KeyCode::Char('d') => mcp::set_disabled(app, client, true),
        KeyCode::Char('e') => mcp::set_disabled(app, client, false),
        KeyCode::Char('r') => mcp::refresh(app, client),
        _ => {}
    }
}

/// The connector auth app owns keys while open (Python `ConnectorAuthApp.BINDINGS`).
pub fn handle_connector_auth_key(app: &mut App, client: &Arc<Client>, key: KeyEvent) {
    match key.code {
        KeyCode::Esc => connector_auth::close(app, client, false),
        KeyCode::Char('r') | KeyCode::Char('R') => connector_auth::refresh(app, client),
        KeyCode::Up | KeyCode::Char('k') => connector_auth::navigate(app, false),
        KeyCode::Down | KeyCode::Char('j') => connector_auth::navigate(app, true),
        KeyCode::Enter => connector_auth::select(app),
        _ => {}
    }
}

/// The MCP OAuth app owns keys while open (Python `MCPOAuthApp.BINDINGS`).
pub fn handle_mcp_oauth_key(app: &mut App, client: &Arc<Client>, key: KeyEvent) {
    match key.code {
        KeyCode::Esc => mcp_oauth::close(app, client),
        KeyCode::Char('r') | KeyCode::Char('R') => mcp_oauth::refresh(app, client),
        KeyCode::Up | KeyCode::Char('k') => mcp_oauth::navigate(app, false),
        KeyCode::Down | KeyCode::Char('j') => mcp_oauth::navigate(app, true),
        KeyCode::Enter => mcp_oauth::select(app),
        _ => {}
    }
}

/// The `/model` picker owns keys: arrows/jk navigate, Enter saves to config,
/// `s` keeps the pick for this session only, Esc cancels.
/// Unlike the theme picker, navigation does not live-preview (Python `ModelPickerApp`).
pub fn handle_model_key(app: &mut App, client: &Arc<Client>, key: KeyEvent) {
    match key.code {
        KeyCode::Esc => model_picker::cancel(app),
        KeyCode::Up | KeyCode::Char('k') => model_picker::navigate(app, false),
        KeyCode::Down | KeyCode::Char('j') => model_picker::navigate(app, true),
        KeyCode::Enter => model_picker::select(app, client, Scope::Saved),
        KeyCode::Char('s') => model_picker::select(app, client, Scope::Session),
        _ => {}
    }
}

/// The `/thinking` picker owns keys: arrows/jk navigate, Enter commits to
/// `enter_scope`, `s` keeps the level for this session only, Esc cancels.
pub fn handle_thinking_key(app: &mut App, client: &Arc<Client>, key: KeyEvent) {
    match key.code {
        KeyCode::Esc => thinking_picker::cancel(app),
        KeyCode::Up | KeyCode::Char('k') => thinking_picker::navigate(app, false),
        KeyCode::Down | KeyCode::Char('j') => thinking_picker::navigate(app, true),
        KeyCode::Enter => {
            let scope = thinking_picker::enter_scope(app);
            thinking_picker::select(app, client, scope);
        }
        KeyCode::Char('s') => thinking_picker::select(app, client, Scope::Session),
        _ => {}
    }
}

/// Hand a commit's answer to the main thread, which releases the commit once it
/// has applied it. With no receiver the commit is released here instead.
pub(crate) async fn deliver<T>(tx: Option<mpsc::Sender<T>>, event: T, pending: &Arc<AtomicU32>) {
    let sent = match tx {
        Some(tx) => tx.send(event).await.is_ok(),
        None => false,
    };
    if !sent {
        pending.fetch_sub(1, Ordering::Relaxed);
    }
}

/// Up-arrow: intercept for history when the caret is on the first visual row (or
/// still on a freshly loaded entry). Ports Textual's `_handle_history_up`; returns
/// true when handled, false to let the caller move the caret up a row instead.
fn handle_history_up(app: &mut App, on_first_row: bool) -> bool {
    let loaded_unmoved =
        app.chat_input.cursor_pos_after_load.is_some() && !app.chat_input.cursor_moved_since_load;
    let should_intercept = on_first_row || loaded_unmoved;
    if !should_intercept {
        return false;
    }
    // Freshly typed text: first Up parks the caret at the start (protecting the
    // draft); only from there does the next Up recall the previous entry.
    if !app.chat_input.input.is_empty() && app.chat_input.cursor != 0 && !loaded_unmoved {
        app.chat_input.cursor = 0;
        app.chat_input.anchor = None;
        mark_cursor_moved(app);
    } else if !message_queue::enter(app) {
        // Queued prompts take the recall Up before input history does.
        recall_previous(app);
    }
    true
}

/// Down-arrow: intercept for history only while on a loaded entry (unmoved, or on
/// the last visual row). Ports Textual's `_handle_history_down`.
fn handle_history_down(app: &mut App, on_last_row: bool) -> bool {
    let on_loaded = app.chat_input.cursor_pos_after_load.is_some();
    let intercept = on_loaded && (!app.chat_input.cursor_moved_since_load || on_last_row);
    if !intercept {
        return false;
    }
    recall_next(app);
    true
}

fn recall_previous(app: &mut App) {
    let navigating = app.chat_input.history.is_navigating();
    let draft = app.chat_input.submitted_text();
    if let Some(entry) = app.chat_input.history.get_previous(&draft) {
        if !navigating {
            let snapshot = crate::edit_history::Snapshot::capture(&app.chat_input);
            app.chat_input.recall_draft = Some(snapshot);
        }
        load_history_entry(app, entry);
    }
}

fn recall_next(app: &mut App) {
    if let Some(entry) = app.chat_input.history.get_next() {
        let navigating = app.chat_input.history.is_navigating();
        // Leaving the recalled entries gives the draft back as it was; moving
        // within them keeps it for later.
        let draft = (!navigating)
            .then(|| app.chat_input.recall_draft.take())
            .flatten();
        match draft {
            Some(draft) => {
                draft.restore(&mut app.chat_input);
                park_after_load(app);
            }
            None => load_history_entry(app, entry),
        }
        if !navigating {
            app.chat_input.cursor_pos_after_load = None;
            app.chat_input.cursor_moved_since_load = false;
        }
    }
}

/// Load a recalled entry: place the caret at the end of its first line and record
/// the loaded-entry state (Textual's `_load_history_entry`).
fn load_history_entry(app: &mut App, entry: String) {
    crate::long_paste::load_collapsed(app, entry);
    crate::composer_paths::rewrite_image_paths(&mut app.chat_input);
    park_after_load(app);
}

/// Park the caret at the end of the first line of text just loaded.
fn park_after_load(app: &mut App) {
    let col = app
        .chat_input
        .input
        .find('\n')
        .unwrap_or(app.chat_input.input.len());
    app.chat_input.cursor = col;
    app.chat_input.anchor = None;
    app.chat_input.cursor_pos_after_load = Some(col);
    app.chat_input.cursor_moved_since_load = false;
    completion_manager::reset_for_recall(app);
}

/// Mark the caret as moved off a loaded history entry so Up/Down edit text next.
fn mark_cursor_moved(app: &mut App) {
    if app.chat_input.cursor_pos_after_load.is_some()
        && !app.chat_input.cursor_moved_since_load
        && app.chat_input.cursor_pos_after_load != Some(app.chat_input.cursor)
    {
        app.chat_input.cursor_moved_since_load = true;
    }
}

/// Clear history navigation and the loaded-entry state after a text edit.
pub(crate) fn reset_history_state(app: &mut App) {
    app.chat_input.history.reset_navigation();
    app.chat_input.recall_draft = None;
    app.chat_input.cursor_pos_after_load = None;
    app.chat_input.cursor_moved_since_load = false;
}

/// Insert a bracketed paste at the cursor as one edit, normalizing CRs; a later
/// existence probe may turn a pasted path list into mentions as a second edit. A
/// paste replaces the active selection, like Textual.
pub fn handle_paste(app: &mut App, text: String) {
    if composer_hidden(app) {
        return;
    }
    let text = text.replace("\r\n", "\n").replace('\r', "\n");
    app.chat_input.normalize_positions();
    let before = crate::edit_history::Snapshot::capture(&app.chat_input);
    let probe = handle_paste_inner(app, &text);
    app.chat_input
        .record_edit(before, true, std::time::Instant::now());
    if let Some((start, raw, candidates)) = probe {
        paste_files::request_probe(app, start, raw, candidates);
    }
    if text.trim().is_empty() {
        paste_image::request(app, false);
    }
}

// Deliberate divergence from Python: the hidden composer takes no paste
// while the list is focused or a child view is open.
pub(crate) fn composer_hidden(app: &App) -> bool {
    app.subagents.list.focused || app.subagents.viewed_subagent_id.is_some()
}

/// Insert `text`; returns the inserted path list's start, text and paths when
/// only an existence probe can tell whether it becomes mentions.
fn handle_paste_inner(app: &mut App, text: &str) -> Option<(usize, String, Vec<String>)> {
    app.chat_input.normalize_positions();
    app.chat_input.scroll = None;
    reset_history_state(app);
    // Pasting a collapsed paste again shows it in full instead of adding a copy.
    if crate::long_paste::expand(app, text) {
        completion_manager::input_changed(app);
        return None;
    }
    let names = app.chat_input.mode.names_images();
    if let Some(paths) = paste_path::pasted_image_paths(text).filter(|_| names) {
        paste_files::insert_images(app, &paths);
        completion_manager::input_changed(app);
        return None;
    }
    app.chat_input.widen_selection();
    if let Some((lo, hi)) = chat_input::selection_range(
        &app.chat_input.input,
        app.chat_input.cursor,
        app.chat_input.anchor,
    ) {
        app.chat_input.input.replace_range(lo..hi, "");
        app.chat_input.cursor = lo;
        app.chat_input.sync_mentions_at(lo);
    }
    app.chat_input.anchor = None;
    let text = app.chat_input.open_pasted_mode(text);
    // A path list stays inline: the existence probe turns it into compact mentions.
    let candidates = match app.chat_input.mode.names_images() {
        true => paste_path::path_candidates(text),
        false => Vec::new(),
    };
    if candidates.is_empty() && crate::long_paste::is_long(text) {
        crate::long_paste::insert_collapsed(app, text);
        completion_manager::input_changed(app);
        return None;
    }
    let start = app.chat_input.cursor;
    input_edit::insert(&mut app.chat_input.input, &mut app.chat_input.cursor, text);
    app.chat_input.sync_mentions_at(start);
    let input = &app.chat_input.input;
    let (prefix, suffix) = (
        input[..start].to_owned(),
        input[app.chat_input.cursor..].to_owned(),
    );
    crate::composer_paths::rewrite_image_paths(&mut app.chat_input);
    completion_manager::input_changed(app);
    let input = &app.chat_input.input;
    let kept = input.len() >= prefix.len() + suffix.len()
        && input.starts_with(&prefix)
        && input.ends_with(&suffix);
    (kept && !candidates.is_empty()).then(|| {
        let raw = input[start..input.len() - suffix.len()].to_owned();
        (start, raw, candidates)
    })
}

/// Ctrl+C ladder (Python `action_interrupt_or_quit`); returns true to exit.
fn handle_ctrl_c(app: &mut App, client: &Arc<Client>) -> bool {
    // Python checks the subagent view before touching the input or the queue.
    if let Some(viewed) = app.subagents.viewed_subagent_id.clone() {
        if app.quit.is_confirmed(QuitConfirmKey::CtrlC) {
            return true;
        }
        // Python appends a read-only error message instead of quitting.
        subagents::append_read_only_message(app, &viewed);
        request_quit_confirmation(app, QuitConfirmKey::CtrlC);
        return false;
    }
    if !app.chat_input.full_text().is_empty() {
        app.chat_input.clear();
        reset_history_state(app);
        completion_manager::input_changed(app);
        app.quit.cancel_confirmation();
        return false;
    }
    if app.quit.is_confirmed(QuitConfirmKey::CtrlC) {
        return true;
    }
    if app.vibe_code_project.open || app.vibe_code_project.pending {
        crate::vibe_code_project::input::handle_key(
            app,
            client,
            KeyEvent::new(KeyCode::Esc, KeyModifiers::NONE),
        );
        return false;
    }
    // Python `_try_interrupt_bottom_app_escape`: reject the approval or cancel the question.
    let esc = KeyEvent::new(KeyCode::Esc, KeyModifiers::NONE);
    if app.approval.open {
        crate::approval::handle_key(app, client, esc);
        return false;
    }
    if app.question_app.open {
        question_input::handle_key(app, client, esc);
        return false;
    }
    // An active narrator stops first; Python's ladder runs it before the queue step.
    if turn_summary::cancel(app) {
        return false;
    }
    // Ladder step before arming quit: drop the newest queued prompt (LIFO).
    if message_queue::pop_last(app, client) {
        return false;
    }
    // Running jobs the loading hint offers to interrupt (Python `_try_interrupt_running_job`).
    if app.session.shell_operation_id.is_some() {
        crate::commands::shell::interrupt(app, client);
        return false;
    }
    if crate::teleport::busy(app) {
        crate::teleport::interrupt(app, client);
        return false;
    }
    if matches!(app.session.status, Status::Generating { .. }) {
        submission::interrupt_turn(app, client);
        return false;
    }
    request_quit_confirmation(app, QuitConfirmKey::CtrlC);
    false
}

/// Ctrl+D (Python `action_delete_right_or_quit`); returns true to exit. Deletes right
/// in the main-view input, else quits (on a second press if confirmation is on).
fn handle_ctrl_d(app: &mut App) -> bool {
    if app.subagents.viewed_subagent_id.is_none() && !app.chat_input.full_text().is_empty() {
        app.chat_input.normalize_positions();
        let before = crate::edit_history::Snapshot::capture(&app.chat_input);
        reset_history_state(app);
        crate::long_paste::dismiss(app);
        app.chat_input
            .edit_atomically(None, |input, cursor, anchor| {
                chat_input::apply(&chat_input::Action::DeleteRight, input, cursor, anchor);
            });
        app.chat_input
            .record_edit(before, false, std::time::Instant::now());
        completion_manager::input_changed(app);
        return false;
    }
    if !app.quit.ask_confirmation_on_exit || app.quit.is_confirmed(QuitConfirmKey::CtrlD) {
        return true;
    }
    request_quit_confirmation(app, QuitConfirmKey::CtrlD);
    false
}

fn request_quit_confirmation(app: &mut App, key: QuitConfirmKey) {
    let queued = app.queue.len();
    app.quit.request_confirmation(key, queued);
}
