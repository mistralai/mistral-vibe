//! Keyboard input handling for the prompt and completion popup.

use std::sync::atomic::{AtomicU32, Ordering};
use std::sync::Arc;

use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};
use tokio::sync::mpsc;

use crate::app::{App, Status};
use crate::commands::{stress, submission};
use crate::mouse::{scroll_chat, KEY_SCROLL_STEP};
use crate::server::{method, Client};
use crate::utils::input_edit;
use crate::{
    agents, chat_input, clipboard, completion_manager, config, connector_auth, feedback, keymap,
    log_level_picker, mcp, mcp_oauth, message_queue, model_picker, paste_image, paste_path,
    question_input, rewind, theme_picker, thinking_picker, turn_summary, voice,
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

/// Copy the selection: Ctrl+Y, Ctrl+Shift+C, or Command+C.
fn is_copy_key(key: &KeyEvent) -> bool {
    let ctrl = key.modifiers.contains(KeyModifiers::CONTROL);
    let command = key.modifiers.contains(KeyModifiers::SUPER);
    let shift = key.modifiers.contains(KeyModifiers::SHIFT);
    (command && key.code == KeyCode::Char('c'))
        || (ctrl
            && ((key.code == KeyCode::Char('y') && !shift)
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
        if app.vibe_code_project.open {
            return None;
        }
        agents::cycle(app, client);
        return Some(false);
    }
    if (is_ctrl_c(&key) || is_copy_key(&key))
        && crate::vibe_code_project::input::copy_selection(app, false)
    {
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
    let Some(text) = chat_input::selected_text(
        &app.chat_input.input,
        app.chat_input.cursor,
        app.chat_input.anchor,
    ) else {
        return false;
    };
    clipboard::copy_to_clipboard(&text);
    true
}

fn composer_owns_key(app: &App) -> bool {
    !app.approval.open
        && !app.question_app.open
        && !app.config_screen.open
        && !app.resume_picker.open
        && !app.mcp.open
        && !app.mcp_oauth.open
        && !app.connector_auth.open
        && !app.rewind.open
        && !app.theme_picker.open
        && !app.model_picker.open
        && !app.log_level_picker.open
        && !app.thinking_picker.open
        && (app.queue.selected.is_none() || app.queue.editing)
}

/// Handle one key press; true means the application should exit.
pub fn handle_key(
    app: &mut App,
    client: &Arc<Client>,
    config_tx: &mpsc::Sender<config::Loaded>,
    key: KeyEvent,
) -> bool {
    if let Some(exit) = handle_priority_key(app, client, key) {
        return exit;
    }
    app.chat_input.normalize_positions();
    // The question app owns keys while the server waits for the user's answer,
    // except the app-level priority bindings that scroll the transcript.
    if app.question_app.open {
        let shift = key.modifiers.contains(KeyModifiers::SHIFT);
        match key.code {
            KeyCode::Up if shift => scroll_chat(app, true, KEY_SCROLL_STEP),
            KeyCode::Down if shift => scroll_chat(app, false, KEY_SCROLL_STEP),
            _ => question_input::handle_key(app, client, key),
        }
        return false;
    }
    // The theme picker owns keys while open.
    if app.theme_picker.open {
        handle_theme_key(app, client, key);
        return false;
    }
    // The model picker owns keys while open.
    if app.model_picker.open {
        handle_model_key(app, client, key);
        return false;
    }
    // The log-level picker owns keys while open.
    if app.log_level_picker.open {
        handle_log_level_key(app, client, key);
        return false;
    }
    // The thinking picker owns keys while open.
    if app.thinking_picker.open {
        handle_thinking_key(app, client, key);
        return false;
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
    // intercepted before any editing key (ADR 0013).
    if app.queue.selected.is_some() && !app.queue.editing {
        message_queue::handle_selection_key(app, client, key);
        return false;
    }
    // Editing a queued prompt: only Escape is special, the rest edits the text.
    if app.queue.editing && key.code == KeyCode::Esc {
        message_queue::end_edit(app);
        return false;
    }
    // Cut the chat input selection or current line; copy is an app-level priority binding.
    if is_cut_key(&key) {
        app.chat_input.scroll = None;
        if let Some(text) = chat_input::cut(
            &mut app.chat_input.input,
            &mut app.chat_input.cursor,
            &mut app.chat_input.anchor,
        ) {
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
    let mode = match key.code {
        KeyCode::Char('!') => Some(crate::input_modes::InputMode::Bash),
        KeyCode::Char('/') => Some(crate::input_modes::InputMode::Slash),
        _ => None,
    };
    let modified = key
        .modifiers
        .intersects(KeyModifiers::CONTROL | KeyModifiers::ALT | KeyModifiers::SUPER);
    if !modified
        && app.chat_input.mode == crate::input_modes::InputMode::Prompt
        && app.chat_input.input.is_empty()
        && mode.is_some()
    {
        reset_history_state(app);
        app.chat_input.mode = mode.unwrap_or_default();
        completion_manager::input_changed(app);
        return false;
    }
    if matches!(key.code, KeyCode::Backspace)
        && app.chat_input.mode != crate::input_modes::InputMode::Prompt
        && app.chat_input.input.is_empty()
        && app.chat_input.cursor == 0
    {
        reset_history_state(app);
        app.chat_input.mode = crate::input_modes::InputMode::Prompt;
        completion_manager::input_changed(app);
        return false;
    }
    // ChatInput editing (keymap -> Action -> apply), decoupled from the backend.
    // An edit resets history recall and the loaded-entry state and refreshes
    // completion; a caret/selection move only marks the caret as moved.
    if let Some(action) = keymap::action_for(&key) {
        app.chat_input.scroll = None;
        let edit = action.is_edit();
        if edit {
            reset_history_state(app);
        }
        chat_input::apply(
            &action,
            &mut app.chat_input.input,
            &mut app.chat_input.cursor,
            &mut app.chat_input.anchor,
        );
        if edit {
            rewrite_image_paths(app);
            completion_manager::input_changed(app);
        } else {
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
        KeyCode::Esc if completion_manager::dismiss(app) => app.overlays.last_escape = None,
        KeyCode::Esc if app.session.shell_operation_id.is_some() => {
            crate::commands::shell::interrupt(app, client);
            app.overlays.last_escape = Some(std::time::Instant::now());
        }
        // Only a docked panel owns Esc; a dropped column must not swallow the interrupt.
        KeyCode::Esc if app.todo_sidebar.visible => app.todo_sidebar.open = false,
        // Escape while generating interrupts the turn (Python `_interrupt_turn`).
        KeyCode::Esc if matches!(app.session.status, Status::Generating { .. }) => {
            submission::interrupt_turn(app, client);
            app.overlays.last_escape = Some(std::time::Instant::now());
        }
        // A summary request is in flight: cancel it, without arming the
        // double-escape (Python `_try_interrupt_no_job_steps`).
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
                app.chat_input.cursor = target;
                app.chat_input.anchor = None;
                mark_cursor_moved(app);
                completion_manager::refresh(app);
            }
        }
        KeyCode::Down => {
            app.chat_input.scroll = None;
            let (target, moved_row) = crate::ui::chat_input::vertical_cursor(app, true);
            if !handle_history_down(app, !moved_row) {
                app.chat_input.cursor = target;
                app.chat_input.anchor = None;
                mark_cursor_moved(app);
                completion_manager::refresh(app);
            }
        }
        KeyCode::Tab => {
            completion_manager::accept(app);
        }
        // A file (`@`) completion accepts on Enter without submitting.
        KeyCode::Enter
            if completion_manager::active_is_file(app) && completion_manager::accept(app) => {}
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
    app.chat_input.cursor = crate::ui::chat_input::page_cursor(app, down);
    mark_cursor_moved(app);
    completion_manager::refresh(app);
}

/// The `/theme` picker owns keys: arrows/jk preview, Enter selects, Esc reverts.
fn handle_theme_key(app: &mut App, client: &Arc<Client>, key: KeyEvent) {
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
        let params = serde_json::json!({
            "sessionId": session_id,
            "ops": [{"op": "set", "path": "/theme", "value": theme, "targetLayer": null}],
            "reason": "app-server config update",
            "reloadRuntime": false,
        });
        let result = client.request(method::CONFIG_WRITE, params).await;
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
        mcp::search_usage::record(app, client);
        return;
    }
    match key.code {
        KeyCode::Esc => mcp::close(app),
        KeyCode::Backspace => mcp::back(app),
        KeyCode::Up | KeyCode::Char('k') => mcp::navigate(app, false),
        KeyCode::Down | KeyCode::Char('j') => mcp::navigate(app, true),
        KeyCode::Enter => mcp::select(app, client),
        KeyCode::Char('d') => mcp::set_disabled(app, client, true),
        KeyCode::Char('e') => mcp::set_disabled(app, client, false),
        KeyCode::Char('r') => mcp::refresh(app, client),
        _ => {}
    }
}

/// The connector auth app owns keys while open (Python `ConnectorAuthApp.BINDINGS`
/// plus its plain `OptionList`, which has no `j`/`k` navigation).
pub fn handle_connector_auth_key(app: &mut App, client: &Arc<Client>, key: KeyEvent) {
    match key.code {
        KeyCode::Esc | KeyCode::Backspace => connector_auth::close(app, client, false),
        KeyCode::Char('r') | KeyCode::Char('R') => connector_auth::refresh(app, client),
        KeyCode::Up => connector_auth::navigate(app, false),
        KeyCode::Down => connector_auth::navigate(app, true),
        KeyCode::Enter => connector_auth::select(app),
        _ => {}
    }
}

/// The MCP OAuth app owns keys while open (Python `MCPOAuthApp.BINDINGS` plus
/// its plain `OptionList`, which has no `j`/`k` navigation).
pub fn handle_mcp_oauth_key(app: &mut App, client: &Arc<Client>, key: KeyEvent) {
    match key.code {
        KeyCode::Esc | KeyCode::Backspace => mcp_oauth::close(app, client, false),
        KeyCode::Char('r') | KeyCode::Char('R') => mcp_oauth::refresh(app, client),
        KeyCode::Up => mcp_oauth::navigate(app, false),
        KeyCode::Down => mcp_oauth::navigate(app, true),
        KeyCode::Enter => mcp_oauth::select(app),
        _ => {}
    }
}

/// The `/model` picker owns keys: arrows/jk navigate, Enter selects, Esc cancels.
/// Unlike the theme picker, navigation does not live-preview (Python `ModelPickerApp`).
fn handle_model_key(app: &mut App, client: &Arc<Client>, key: KeyEvent) {
    match key.code {
        KeyCode::Esc => model_picker::cancel(app),
        KeyCode::Up | KeyCode::Char('k') => model_picker::navigate(app, false),
        KeyCode::Down | KeyCode::Char('j') => model_picker::navigate(app, true),
        KeyCode::Enter => select_model(app, client),
        _ => {}
    }
}

/// Commit the highlighted model: close the picker and persist the alias (`""`
/// for Default/unpinned) via `config/write`, mirroring Python's `_persist_model`.
/// The written runtime comes back on the response, then a `config/reload` follows
/// (Python `_reload_config`); both runtimes are applied on the main thread.
fn select_model(app: &mut App, client: &Arc<Client>) {
    let alias = model_picker::selected_alias(app);
    app.model_picker.open = false;
    let Some(session_id) = app.session.session_id.clone() else {
        return;
    };
    let client = client.clone();
    let model_tx = app.model_picker.tx.clone();
    let pending = app.commit_started();
    tokio::spawn(async move {
        let params = serde_json::json!({
            "sessionId": session_id.clone(),
            "ops": [{"op": "set", "path": "/active_model", "value": alias, "targetLayer": null}],
            "reason": "app-server config update",
            "reloadRuntime": false,
        });
        let written = client.request(method::CONFIG_WRITE, params).await;
        let reload = serde_json::json!({
            "sessionId": session_id,
            "reloadRuntime": true,
        });
        let reloaded = match written.is_ok() {
            true => client.request(method::CONFIG_RELOAD, reload).await.ok(),
            false => None,
        };
        // Only the final runtime reaches the UI: the written one is superseded
        // before it could be seen, and one event keeps the commit's last paint
        // and the idle marker in step.
        let event = match (reloaded, written) {
            (Some(runtime), _) => model_picker::Event::Reloaded(runtime),
            (None, Ok(runtime)) => model_picker::Event::Written(runtime),
            (None, Err(_)) => model_picker::Event::Failed,
        };
        deliver(model_tx, event, &pending).await;
    });
}

/// The `/thinking` picker owns keys: arrows/jk navigate, Enter selects, Esc cancels.
fn handle_thinking_key(app: &mut App, client: &Arc<Client>, key: KeyEvent) {
    match key.code {
        KeyCode::Esc => thinking_picker::cancel(app),
        KeyCode::Up | KeyCode::Char('k') => thinking_picker::navigate(app, false),
        KeyCode::Down | KeyCode::Char('j') => thinking_picker::navigate(app, true),
        KeyCode::Enter => select_thinking(app, client),
        _ => {}
    }
}

/// Commit the highlighted thinking level: close the picker and persist via
/// `config/write` to `/models/<alias>/thinking`, then `config/reload`.
fn select_thinking(app: &mut App, client: &Arc<Client>) {
    let level = thinking_picker::selected_level(app).to_owned();
    app.thinking_picker.open = false;
    let Some(session_id) = app.session.session_id.clone() else {
        return;
    };
    let alias = app.model_picker.current_model.clone();
    if alias.is_empty() {
        return;
    }
    let alias = escape_json_pointer(&alias);
    let client = client.clone();
    let thinking_tx = app.thinking_picker.tx.clone();
    let pending = app.commit_started();
    tokio::spawn(async move {
        let path = format!("/models/{alias}/thinking");
        let failure_prefix = format!("Failed to apply: thinking {level} — ");
        let params = serde_json::json!({
            "sessionId": session_id.clone(),
            "ops": [{"op": "set", "path": path, "value": level, "targetLayer": null}],
            "reason": "app-server thinking update",
            "reloadRuntime": false,
        });
        let written = client.request(method::CONFIG_WRITE, params).await;
        // Python raises on a rejected or failed mutation even though the RPC
        // succeeds (`ConfigMutationResponse`), and `_run_settings_update`
        // mounts the error; only a clean write is worth reloading.
        let event = match written {
            Err(error) => thinking_failed(&failure_prefix, &error.to_string()),
            Ok(value) => match config_mutation_error(&value) {
                Some(error) => thinking_failed(&failure_prefix, &error),
                None => {
                    let reload = serde_json::json!({
                        "sessionId": session_id,
                        "reloadRuntime": true,
                    });
                    match client.request(method::CONFIG_RELOAD, reload).await {
                        Ok(runtime) => thinking_picker::Event::Reloaded(runtime),
                        // Python's `_run_settings_update` wraps write + reload
                        // in one try/except: a reload failure mounts the same
                        // "Failed to apply" error as a write rejection.
                        Err(error) => thinking_failed(&failure_prefix, &error.to_string()),
                    }
                }
            },
        };
        deliver(thinking_tx, event, &pending).await;
    });
}

/// Python `_run_settings_update`: a failed update mounts an `ErrorMessage`.
fn thinking_failed(prefix: &str, error: &str) -> thinking_picker::Event {
    thinking_picker::Event::Failed(format!("{prefix}{error}"))
}

/// Python raises `AppServerResponseError` on a rejected or failed config mutation.
fn config_mutation_error(value: &serde_json::Value) -> Option<String> {
    if value
        .get("rejected")
        .and_then(serde_json::Value::as_bool)
        .unwrap_or(false)
    {
        return Some("Invalid configuration edit".to_owned());
    }
    let failures: Vec<&str> = value
        .get("failures")
        .and_then(serde_json::Value::as_array)
        .map(|items| items.iter().filter_map(serde_json::Value::as_str).collect())
        .unwrap_or_default();
    (!failures.is_empty()).then(|| failures.join("; "))
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
    let draft = app.chat_input.full_text();
    if let Some(entry) = app.chat_input.history.get_previous(&draft) {
        load_history_entry(app, entry);
    }
}

fn recall_next(app: &mut App) {
    if let Some(entry) = app.chat_input.history.get_next() {
        let navigating = app.chat_input.history.is_navigating();
        load_history_entry(app, entry);
        if !navigating {
            app.chat_input.cursor_pos_after_load = None;
            app.chat_input.cursor_moved_since_load = false;
        }
    }
}

/// Load a recalled entry: place the caret at the end of its first line and record
/// the loaded-entry state (Textual's `_load_history_entry`).
fn load_history_entry(app: &mut App, entry: String) {
    let entry = paste_path::rewrite_bare_image_paths_in_text(&entry);
    app.chat_input.load_full_text(entry);
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
    app.chat_input.cursor_pos_after_load = None;
    app.chat_input.cursor_moved_since_load = false;
}

/// Insert a bracketed paste at the cursor as a single edit, normalizing CRs. A
/// paste replaces the active selection, like Textual.
pub fn handle_paste(app: &mut App, text: String) {
    app.chat_input.normalize_positions();
    app.chat_input.scroll = None;
    reset_history_state(app);
    let text = text.replace("\r\n", "\n").replace('\r', "\n");
    let probe_clipboard = text.trim().is_empty();
    let rewritten = paste_path::maybe_prepend_at_for_image_path(&text);
    let image_path = rewritten != text;
    if let Some((lo, hi)) = chat_input::selection_range(
        &app.chat_input.input,
        app.chat_input.cursor,
        app.chat_input.anchor,
    ) {
        app.chat_input.input.replace_range(lo..hi, "");
        app.chat_input.cursor = lo;
    }
    app.chat_input.anchor = None;
    let text = match image_path {
        true => paste_path::with_image_mention_boundaries(
            &app.chat_input.input,
            app.chat_input.cursor,
            &rewritten,
        ),
        false => rewritten,
    };
    input_edit::insert(&mut app.chat_input.input, &mut app.chat_input.cursor, &text);
    rewrite_image_paths(app);
    completion_manager::input_changed(app);
    if probe_clipboard {
        paste_image::request(app, false);
    }
}

fn rewrite_image_paths(app: &mut App) {
    let rewritten = paste_path::rewrite_bare_image_paths_in_text(&app.chat_input.input);
    if rewritten == app.chat_input.input {
        return;
    }
    app.chat_input.input = rewritten;
    app.chat_input.cursor = app.chat_input.input.len();
    app.chat_input.anchor = None;
}

/// Ctrl+C ladder (Python `action_interrupt_or_quit`); returns true to exit.
fn handle_ctrl_c(app: &mut App, client: &Arc<Client>) -> bool {
    if !app.chat_input.full_text().is_empty() {
        app.chat_input.clear();
        reset_history_state(app);
        completion_manager::input_changed(app);
        app.overlays.quit_pending = None;
        return false;
    }
    if app.quit_confirm_active() {
        return true;
    }
    if (app.vibe_code_project.open || app.vibe_code_project.pending) && !app.server_closed {
        crate::vibe_code_project::input::handle_key(
            app,
            client,
            KeyEvent::new(KeyCode::Esc, KeyModifiers::NONE),
        );
        return false;
    }
    // A summary request is in flight: cancel it (Python's ladder runs the
    // narrator step before touching the queue).
    if turn_summary::cancel(app) {
        return false;
    }
    // Ladder step before arming quit: drop the newest queued prompt (LIFO).
    if message_queue::pop_last(app, client) {
        return false;
    }
    if app.session.shell_operation_id.is_some() {
        crate::commands::shell::interrupt(app, client);
        return false;
    }
    app.overlays.quit_pending = Some(std::time::Instant::now());
    false
}

/// Ctrl+D (Python `action_delete_right_or_quit`); returns true to exit. With text
/// it deletes the char under the cursor; on empty input it arms/confirms quit.
fn handle_ctrl_d(app: &mut App) -> bool {
    if !app.chat_input.full_text().is_empty() {
        reset_history_state(app);
        chat_input::apply(
            &chat_input::Action::DeleteRight,
            &mut app.chat_input.input,
            &mut app.chat_input.cursor,
            &mut app.chat_input.anchor,
        );
        completion_manager::input_changed(app);
        return false;
    }
    if app.quit_confirm_active() {
        return true;
    }
    app.overlays.quit_pending = Some(std::time::Instant::now());
    false
}

/// Escape a JSON pointer token (Python `_escape_json_pointer_token`).
fn escape_json_pointer(value: &str) -> String {
    value.replace('~', "~0").replace('/', "~1")
}
