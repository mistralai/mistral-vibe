//! Reduce startup and app-server events into UI state.

use std::sync::Arc;

use crate::server::{notification, server_method};
use crate::server::{Client, EntryHead, EntryParams, Notification, PublicTurn};
use serde::Deserialize;
use serde_json::Value;
use tokio::sync::mpsc;

use crate::app::{App, Status, ToastSeverity};
use crate::cli::StartupResume;
use crate::commands::compact;
use crate::commands::retry;
use crate::commands::retry_continuation;
use crate::commands::submission;
use crate::commands::submission::new_message_id;
use crate::startup::StartupEvent;
use crate::transcript::local;
use crate::utils::startup_cache;
use crate::{
    agents, approval, artifacts, completion_manager, config, config_issues, feedback, mcp,
    message_queue, model_picker, observability, post_ready, question_app, resume_picker,
    session_exit, startup, subagents, todo_tracker, turn_summary, ui,
};

/// Seconds a server warning/error toast stays up.
pub(crate) const SERVER_TOAST_SECS: u64 = 5;
/// The `warning` code of a connector or MCP server that failed to load.
const INTEGRATIONS_UNAVAILABLE: &str = "integrations_unavailable";

/// Returns true if a deferred `/exit` was replayed during the Ready event.
pub fn apply_startup_event(
    app: &mut App,
    client: &Arc<Client>,
    config_tx: &mpsc::Sender<config::Loaded>,
    event: StartupEvent,
) -> bool {
    match event {
        StartupEvent::Trust(details) => {
            app.session.trust_prompted = true;
            crate::trust_folders::open(app, *details);
            false
        }
        StartupEvent::Attached(session_id) => {
            app.set_session_id(session_id.clone());
            if app.config_screen.open {
                config::load(session_id, client, config_tx, true);
            }
            false
        }
        StartupEvent::Sessions(value) => {
            resume_picker::open_listed(app, client, &value);
            false
        }
        StartupEvent::Config(config) => {
            show_startup_config(app, *config);
            false
        }
        StartupEvent::Ready(ready) => apply_ready(app, client, config_tx, *ready),
        StartupEvent::MissingApiKey { .. } => {
            // The wizard is a pre-session surface:
            // the loop exits on this verdict and the entrypoint reruns the
            // wizard, so the reducer only logs.
            tracing::warn!(vibe_boundary = "startup", "missing API key; setup required");
            false
        }
        StartupEvent::ConfigError(message) => {
            let message = format!("Invalid configuration: {message}");
            tracing::error!(
                vibe_boundary = "startup",
                fatal = true,
                "config error: {message}"
            );
            app.session.startup_error = Some(message);
            app.view.transcript.remove(RESUME_STATUS_ID);
            app.set_status(Status::Failed);
            false
        }
        StartupEvent::Failed(error) => {
            tracing::error!(
                vibe_boundary = "startup",
                fatal = true,
                "startup failed: {error}"
            );
            app.session.startup_error = Some(error);
            app.view.transcript.remove(RESUME_STATUS_ID);
            app.set_status(Status::Failed);
            false
        }
    }
}

/// The auto-resume status row, settled in place once the attach lands.
const RESUME_STATUS_ID: &str = "rs-startup-resume";

/// `--continue`/`--resume <id>` show a resume status while the handshake attaches.
pub fn announce_resume(app: &mut App) {
    if matches!(
        app.session.startup_resume,
        StartupResume::ById(_) | StartupResume::Continue
    ) {
        local::add_progress_status(
            &mut app.view.transcript,
            RESUME_STATUS_ID,
            "Resuming session\u{2026}",
        );
    }
}

/// The borderless warning mounted
/// at startup when the cwd is a risky folder.
pub fn show_dangerous_directory_warning(app: &mut App) {
    if let Some(reason) = crate::utils::paths::dangerous_directory_reason() {
        let warning =
            format!("⚠ WARNING: {reason}\n\nRunning in this location is not recommended.");
        local::add_warning(
            &mut app.view.transcript,
            &submission::new_message_id(),
            &warning,
        );
    }
}

/// One detached, non-forced update check after startup, its outcome only written to the update
/// cache. Failures are logged, never surfaced. Replay runs have no PyPI, so
/// the check stays offline there like the other replay-gated subsystems.
fn spawn_background_update_check(enabled: bool) {
    if !enabled || crate::utils::is_replaying() {
        return;
    }
    tokio::spawn(async move {
        let repository = crate::update_notifier::cache::FileSystemUpdateCacheRepository;
        let gateway =
            crate::update_notifier::gateway::UpdateCheckGateway::for_project("mistral-vibe").await;
        let now = crate::utils::now_unix();
        if let Err(error) = crate::update_notifier::update::get_update_if_available(
            &gateway,
            env!("CARGO_PKG_VERSION"),
            &repository,
            now,
            false,
        )
        .await
        {
            // A failed cache write never reaches here: it is logged inside.
            tracing::warn!("Update check failed: {}", error.message)
        }
    });
}

/// Settle the auto-resume status with what the attach did; a failure becomes an error row.
fn attach_notice(app: &mut App, attach: startup::Attach) {
    let transcript = &mut app.view.transcript;
    let text = match attach {
        startup::Attach::Started => return transcript.remove(RESUME_STATUS_ID),
        startup::Attach::Resumed(session) => {
            format!("Resumed session {}.", resume_picker::short_id(&session))
        }
        startup::Attach::NoPrevious => "No previous sessions found.".to_owned(),
        startup::Attach::Failed(message) => {
            transcript.remove(RESUME_STATUS_ID);
            let id = submission::new_message_id();
            return local::add_command_error(transcript, &id, &message);
        }
    };
    if !transcript.contains(RESUME_STATUS_ID) {
        local::add_progress_status(transcript, RESUME_STATUS_ID, &text);
    }
    local::settle_status(transcript, RESUME_STATUS_ID, &text);
}

fn apply_ready(
    app: &mut App,
    client: &Arc<Client>,
    config_tx: &mpsc::Sender<config::Loaded>,
    ready: startup::Ready,
) -> bool {
    let startup::Ready {
        startup_cache,
        config,
        state,
        attach,
        runtime,
        is_cold_start,
        settled,
        prepared,
        absorbed,
    } = ready;
    observability::level::set_config_log_level(config.log_level.as_deref());
    observability::sentry::init_sentry(
        config.enable_telemetry,
        false,
        [("entrypoint".to_owned(), "vibe-rs".to_owned())].into(),
    );
    let state = match serde_json::from_value::<crate::server::PublicSessionState>(state) {
        Ok(state) => state,
        Err(error) => {
            app.session.startup_error = Some(format!("invalid session state: {error}"));
            app.set_status(Status::Failed);
            return false;
        }
    };
    crate::terminal_notifier::configure(app, &runtime);
    if !app.session.resumed {
        let title = runtime["sessionLog"]["title"].as_str();
        let title = title.or(state.session.title.as_deref()).unwrap_or("");
        app.terminal_notifier.set_default_title(title);
    }
    app.subagents.status_list_enabled = startup::read_show_subagent_status_list(&runtime);
    app.quit.ask_confirmation_on_exit = startup::read_ask_confirmation_on_exit(&runtime);
    apply_final_startup_config(app, startup_cache);
    app.view
        .transcript
        .set_show_reasoning(startup::read_show_thinking_nodes(&runtime));
    // The picker can resume before the handshake finishes, and that session
    // wins: the one started here is only what Esc would fall back to.
    if !app.session.resumed {
        message_queue::reconcile_snapshot(app, client, &state);
        app.set_session_id(state.session.id.clone());
        app.subagents.seed_snapshot(state.child_sessions.clone());
        subagents::refresh(app);
        match app.resume_picker.open {
            true => resume_picker::rebase(app, &state),
            false => {
                app.todo_tracker.seed_from_history(state.history.as_ref());
                resume_picker::load_history(app, &state);
                crate::worktree::track_state(app, &state);
            }
        }
    }
    app.session.tokens = startup::read_tokens(&runtime);
    app.subagents.main_tokens = app.session.tokens;
    app.session.stats = crate::utils::json::read(&runtime["runtime"]["stats"]);
    // This run's `--worktree` verdict (the announce), pinned apart from the
    // session's own tracking: a resumed session's restored effect carries the
    // original run's `created`, and reuse must stay non-cleaning.
    app.session.prepared_worktree = prepared;
    // The notifications the settle wait absorbed, applied once the state they
    // follow exists, so the footer lands on the worktree before first paint.
    for notification in absorbed {
        if !apply_notification(app, client, &notification) {
            tracing::debug!("absorbed notification deferred; no approval is pending pre-TUI");
        }
    }
    // Later picker resumes resend this config, so it must keep naming the
    // settled worktree, not the checkout the run started in.
    if let Some(cwd) = settled {
        app.session.agent_config.cwd = Some(cwd);
    }
    // Snapshot the usage baseline once the session attaches; a picker resume
    // that already adopted keeps the baseline it set.
    if !app.session.resumed {
        app.session.usage_baseline = Some(session_exit::current_usage(app));
    }
    app.completion.skills = startup::read_skills(&runtime);
    app.voice.mode_enabled = config.voice_mode_enabled;
    app.voice.transcription = config.transcription;
    crate::narrator_manager::apply_runtime(app, &runtime);
    model_picker::apply_runtime(app, &runtime);
    crate::thinking_picker::apply_runtime(app, &runtime);
    agents::apply_runtime(app, &runtime);
    agents::flush_pending(app, client);
    mcp::show_post_init_notices(app, &runtime);
    config_issues::show_config_issues(app, &runtime);
    startup::banners::mount(app, &runtime, client);
    // Read the what's-new gate inside mount before scheduling the update
    // check; keeping the spawn after mount preserves that ordering.
    // ADR 0015: apply the live trust policy before the check's gateway
    // builds its client.
    crate::update_notifier::gateway::configure_tls_trust(config.enable_system_trust_store);
    spawn_background_update_check(config.enable_update_checks);
    post_ready::fetch(app, client, config.show_greeting);
    artifacts::fetch(app, client);
    resume_picker::subscribe(app, client);
    completion_manager::refresh(app);
    app.set_status(Status::Ready);
    approval::show_pending(app);
    record_startup(app, &attach, is_cold_start);
    attach_notice(app, attach);
    crate::commands::shell::flush_pending(app, client);
    message_queue::flush_pending(app, client);
    // The resume picker owns the startup: the prompt waits for the session the
    // user picks, or is dropped when they cancel.
    if !app.resume_picker.open {
        process_initial_prompt(app, client);
    }
    // Prompts flush before deferred slash commands; a prompt-then-command typed
    // during Starting thus dispatches in that order regardless of submission
    // order.
    submission::flush_pending(app, client, config_tx)
}

/// Startup telemetry, sent once. Fields this client has no surface for
/// (teleport) are honestly `false`; `session_init_duration_ms` stays null since
/// the server does not report it to this client.
fn record_startup(app: &mut App, attach: &startup::Attach, is_cold_start: bool) {
    let properties = crate::telemetry::bmap(serde_json::json!({
        "agent_ready_duration_ms": startup::agent_ready_duration_ms(),
        "session_init_duration_ms": Value::Null,
        "has_initial_prompt": app.session.initial_prompt.as_deref().is_some_and(|p| !p.is_empty()),
        "teleport_on_start": false,
        "show_resume_picker": app.session.resume_picker_shown,
        "is_resuming_session": matches!(attach, startup::Attach::Resumed(_)),
        "prompt_for_workspace_trust": app.session.trust_prompted,
        "is_cold_start": is_cold_start,
        "mouse_capture": app.session.startup_config.enable_mouse_capture,
    }));
    app.session.startup_telemetry = Some(properties);
    flush_startup_telemetry(app);
}

/// Send the held `vibe.startup` once the first frame exists: a `--worktree` run
/// replays `Ready` before its first draw.
pub fn flush_startup_telemetry(app: &mut App) {
    let Some(first_frame) = startup::first_frame_duration_ms() else {
        return;
    };
    let Some(mut properties) = app.session.startup_telemetry.take() else {
        return;
    };
    properties.insert("first_frame_duration_ms".into(), first_frame.into());
    crate::telemetry::record(app, crate::telemetry::event::STARTUP, properties);
}

/// Send the positional `vibe <prompt>` argument into the session, once as a
/// plain prompt, or teleport it under `--teleport`.
pub fn process_initial_prompt(app: &mut App, client: &Arc<Client>) {
    // An empty prompt is dropped.
    let prompt = app
        .session
        .initial_prompt
        .take()
        .filter(|prompt| !prompt.is_empty());
    if std::mem::take(&mut app.session.teleport_on_start)
        && crate::commands::has_command("/teleport")
    {
        crate::teleport::submit(app, client, prompt.unwrap_or_default());
    } else if let Some(prompt) = prompt {
        message_queue::enqueue_prompt(app, client, prompt);
    }
}

/// Reduce a `runtime` value (`{runtime: {...}}`) into UI state: the startup
/// config projection, the `/model` picker snapshot and the agent list. Shared by
/// `runtime/updated` and the `config/write` response committed from the model picker.
pub fn apply_runtime_value(app: &mut App, runtime: &Value) {
    crate::terminal_notifier::configure(app, runtime);
    app.subagents.status_list_enabled = startup::read_show_subagent_status_list(runtime);
    app.quit.ask_confirmation_on_exit = startup::read_ask_confirmation_on_exit(runtime);
    let show_thinking = startup::read_show_thinking_nodes(runtime);
    app.view.transcript.set_show_reasoning(show_thinking);
    app.subagents.transcripts.set_show_reasoning(show_thinking);
    subagents::refresh(app);
    let narrator_enabled = app.session.startup_config.narrator_enabled;
    let version = app.session.startup_config.server_version.clone();
    if let Some(config) = startup_cache::StartupConfig::from_runtime(&version, runtime) {
        apply_final_startup_config(app, config);
    }
    crate::voice_app::apply_runtime(app, runtime);
    crate::narrator_manager::apply_runtime(app, runtime);
    if narrator_enabled != app.session.startup_config.narrator_enabled {
        turn_summary::cancel(app);
    }
    app.completion.skills = startup_cache::read_skills(runtime);
    model_picker::apply_runtime(app, runtime);
    crate::thinking_picker::apply_runtime(app, runtime);
    agents::apply_runtime(app, runtime);
    app.session.runtime = runtime.clone();
    app.session.stats = crate::utils::json::read(&runtime["runtime"]["stats"]);
    completion_manager::refresh(app);
    mcp::apply_runtime(app, runtime);
}

/// Apply a mutation response's `runtime` when it carries one.
pub fn apply_response_runtime(app: &mut App, response: &Value) {
    if response.get("runtime").is_some_and(Value::is_object) {
        apply_runtime_value(app, response);
        config_issues::show_updated_config_warnings(app, response);
    }
}

fn apply_final_startup_config(app: &mut App, config: startup_cache::StartupConfig) {
    if let Err(err) = config.update_cache() {
        tracing::warn!(%err, "failed to update startup config cache");
    }
    show_startup_config(app, config);
}

fn show_startup_config(app: &mut App, config: startup_cache::StartupConfig) {
    ui::theme::set_active(&config.theme);
    app.session.tokens.1 = config.context_window;
    app.completion.skills.clone_from(&config.skills);
    agents::show_startup_agents(app, &config);
    app.view.banner.sync_model_variant(&config);
    app.session.startup_config = config;
    completion_manager::refresh(app);
}

/// A live reasoning row starts with the bulk fold state; other live entries
/// keep their default collapsed state.
fn expand_live_reasoning(app: &mut App, entry: &EntryHead) {
    if app.view.tools_collapsed {
        return;
    }
    let Some(id) = entry.id.as_deref() else {
        return;
    };
    if app.view.transcript.is_reasoning(id) {
        app.view.expanded.insert(id.to_owned());
        app.view.transcript_cache.invalidate_layouts();
    }
}

/// A settled todo effect replaces the pinned list.
fn record_todos(app: &mut App, raw: Option<&Value>) {
    let Some(todos) = raw.and_then(todo_tracker::todos_from_entry) else {
        return;
    };
    app.todo_tracker.record(todos);
}

/// The stored entry an update points at, cloned out before `record_todos` takes `&mut app`.
fn updated_entry_raw(app: &App, params: &EntryParams) -> Option<Value> {
    let id = params.entry_id.as_deref()?;
    app.view.transcript.entry_raw(id).cloned()
}

fn sync_loading_label(app: &mut App, params: &EntryParams) {
    if !matches!(app.session.status, Status::Generating { .. }) {
        return;
    }
    let entry = &params.entry;
    match entry.entry_type() {
        Some("reasoning") => {
            app.view
                .loading
                .set_label(ui::loading::THINKING_LOADING_STATUS);
        }
        // Hook notices steer the loading status; other detail kinds
        // leave it untouched, so nothing else resets it here.
        Some("notice") => match entry.detail_kind() {
            Some("hook_started") => {
                let name = entry.detail.hook_name.as_deref().unwrap_or_default();
                app.view
                    .loading
                    .set_label(format!("Running hook {name}").trim_end());
            }
            Some("hook_completed") => {
                app.view
                    .loading
                    .set_label(ui::loading::DEFAULT_LOADING_STATUS);
            }
            _ => {}
        },
        // A starting call shows its `statusText`; the status resets to the
        // default once a settled result lands, but only when no other call is
        // live.
        Some("effect") => {
            let id = entry.id.as_deref().unwrap_or("");
            if entry.generation_status.as_deref() == Some("in_progress") {
                let status = entry.detail.display.status_text.as_deref();
                app.view.loading.set_label(status.unwrap_or_default());
            } else if !app.view.transcript.has_live_tool_calls(id) {
                app.view
                    .loading
                    .set_label(ui::loading::DEFAULT_LOADING_STATUS);
            }
        }
        _ => {
            // An `entryUpdated` patch carries no entry; the merged effect still
            // re-states its label, and a settling one resets it unless another
            // call is still live.
            let id = params.entry_id.as_deref().unwrap_or("");
            match app.view.transcript.effect_loading_status(id) {
                Some((status, false)) => app.view.loading.set_label(&status),
                Some((_, true)) => {
                    if !app.view.transcript.has_live_tool_calls(id) {
                        app.view
                            .loading
                            .set_label(ui::loading::DEFAULT_LOADING_STATUS);
                    }
                }
                None => app
                    .view
                    .loading
                    .set_label(ui::loading::DEFAULT_LOADING_STATUS),
            }
        }
    }
}

/// A snapshot with `retrying` set swaps
/// the label in; a null one restores only when the label currently says Retrying.
fn sync_retrying_label(app: &mut App, state: &crate::server::PublicSessionState) {
    if !matches!(app.session.status, Status::Generating { .. }) {
        return;
    }
    match state.retrying {
        Some(_) => app
            .view
            .loading
            .set_label(ui::loading::RETRYING_LOADING_STATUS),
        None => {
            if app.view.loading.label() == ui::loading::RETRYING_LOADING_STATUS {
                app.view
                    .loading
                    .set_label(ui::loading::DEFAULT_LOADING_STATUS);
            }
        }
    }
}

fn is_foreign_session_notification(app: &App, event: &Notification) -> bool {
    if !matches!(
        event.method.as_str(),
        notification::HISTORY_ENTRY_ADDED
            | notification::HISTORY_ENTRY_UPDATED
            | notification::TURN_STARTED
            | notification::TURN_COMPLETED
            | notification::TURN_QUEUE_UPDATED
            | notification::SESSION_STATS_UPDATED
            | notification::SESSION_SNAPSHOT
            | notification::CHILD_SESSION_UPDATED
            | notification::ARTIFACTS_CREATED
            | notification::ARTIFACTS_UPDATED
    ) {
        return false;
    }
    let Some(session_id) = event.params["sessionId"].as_str() else {
        return false;
    };
    app.session
        .session_id
        .as_deref()
        .is_some_and(|current| current != session_id)
}

/// Apply one notification, or return `false` when the event loop must retain it.
pub fn apply_notification(app: &mut App, client: &Arc<Client>, event: &Notification) -> bool {
    // Lease updates apply pre-Starting: the startup resume picker is open while
    // the engine comes up, and its locks must not miss them.
    if event.method == notification::SESSION_LEASE_UPDATED {
        resume_picker::on_lease_updated(app, client, &event.params);
        return true;
    }
    if event.method == notification::SESSION_LEASE_ENDED {
        resume_picker::on_lease_ended(app);
        return true;
    }
    if event.method == notification::REPLAY_DRAINED {
        if let Some(id) = event.params["requestId"].as_u64() {
            client.mark_replay_drained(id);
        }
        return true;
    }
    if event.method == server_method::CALLBACK_CALL
        && event.params["callback"]["detail"]["kind"] == "approval"
    {
        return approval::on_callback_call(app, &event.params);
    }
    if event.method == notification::SESSION_UPDATED {
        // Applied before the Starting gate: the worktree move can land between
        // session/start and the handshake's last read, and the state-only
        // updates are safe pre-ready.
        if let Some(patch) = event.params.get("patch") {
            crate::worktree::track_patch(app, patch);
        }
        if let Some(title) = crate::terminal_notifier::updated_title(&event.params) {
            app.terminal_notifier.set_default_title(title);
        }
        return true;
    }
    // Warnings/errors are transient toasts, never re-sent in the snapshot: surface them pre-Ready too.
    if matches!(app.session.status, Status::Starting | Status::Failed)
        && !matches!(
            event.method.as_str(),
            notification::WARNING | notification::ERROR
        )
    {
        return true;
    }
    if is_foreign_session_notification(app, event) {
        return true;
    }
    match event.method.as_str() {
        notification::HISTORY_ENTRY_ADDED => {
            let params = EntryParams::read(&event.params);
            let entry = &params.entry;
            if entry.detail_kind() == Some("session_title_updated") {
                if let Some(title) = &entry.detail.title {
                    app.terminal_notifier.set_default_title(title);
                }
            }
            if let Some(entry_id) = &entry.id {
                crate::commands::shell::apply_started(app, client, entry_id);
            }
            if entry_finishes_reasoning(entry) {
                app.view.transcript.settle_reasoning();
            }
            message_queue::steering_history_added(app, client, &event.params);
            retry::on_entry_added(app, entry);
            app.view.transcript.add(&event.params);
            crate::program_status::shell_history_updated(app);
            crate::worktree::track_entry(app, &event.params);
            record_todos(app, event.params.get("entry"));
            retry_continuation::merge_continuation(app);
            // Track the turn's assistant message inside the resolve, so a
            // continuation's hidden entry maps to the merged row it feeds.
            retry::track_turn_assistant(app, entry);
            expand_live_reasoning(app, entry);
            sync_loading_label(app, &params);
            turn_summary::track_narrator_added(app, &event.params, entry);
            if let Some(entry) = event.params.get("entry") {
                subagents::remember_parent_instruction_from_entry(app, entry);
            }
        }
        notification::HISTORY_ENTRY_UPDATED => {
            let params = EntryParams::read(&event.params);
            app.view.transcript.update(&event.params);
            crate::program_status::shell_history_updated(app);
            crate::worktree::track_entry(app, &event.params);
            // The wire patch carries no entry; the stored one is post-patch.
            let entry = updated_entry_raw(app, &params);
            record_todos(app, entry.as_ref());
            retry_continuation::merge_continuation(app);
            sync_loading_label(app, &params);
            turn_summary::track_narrator_updated(app, &event.params);
            if let Some(entry) = updated_entry_raw(app, &params) {
                subagents::remember_parent_instruction_from_entry(app, &entry);
            }
        }
        notification::SESSION_SNAPSHOT => {
            if let Some(state) = event.params.get("state") {
                if let Ok(state) = crate::server::PublicSessionState::deserialize(state) {
                    message_queue::reconcile_snapshot(app, client, &state);
                    // ADR 0009: a live same-session snapshot retains the loaded
                    // contiguous prefix and merges the page into it; a
                    // handoff or resync still replaces it.
                    let same_session =
                        app.session.session_id.as_deref() == Some(state.session.id.as_str());
                    match same_session {
                        true => app.view.transcript.load_live_snapshot(&state),
                        false => {
                            // A handoff or resync adopts another harness session.
                            app.todo_tracker.seed_from_history(state.history.as_ref());
                            resume_picker::load_history(app, &state);
                        }
                    }
                    crate::worktree::track_state(app, &state);
                    retry_continuation::reconcile_snapshot(app, same_session);
                    sync_retrying_label(app, &state);
                    app.expand_rebuilt_tools();
                    app.subagents.seed_snapshot(state.child_sessions.clone());
                    if !same_session {
                        subagents::reset_views(app);
                    }
                    subagents::refresh(app);
                }
            }
        }
        notification::TURN_STARTED => {
            let turn = PublicTurn::read(&event.params);
            app.session.active_turn_id = turn.id;
            let interrupt = message_queue::take_interrupt_on_start(app);
            if let Some(queue_item_id) = turn.queue_item_id {
                if message_queue::turn_started(app, client, &queue_item_id) {
                    feedback::maybe_show(app, client, app.session.session_id.clone());
                }
            }
            app.set_status(Status::Generating {
                since: std::time::Instant::now(),
            });
            // An unsolicited turn ends the previous turn's open data, drops
            // whatever summary it still had in flight, and opens a fresh one.
            turn_summary::on_turn_end(app, client);
            turn_summary::cancel(app);
            turn_summary::on_turn_start(app, "");
            // The user interrupted this turn before it started.
            if interrupt {
                app.set_status(Status::Ready);
                submission::send_interrupt(app, client);
            }
        }
        // The server promotes the next queued prompt itself; the loading area goes
        // idle until its `turn/started` arrives.
        notification::TURN_COMPLETED => {
            let turn = PublicTurn::read(&event.params);
            let status = turn.status();
            let context = if status == Some("failed") {
                crate::terminal_notifier::NotificationContext::Failed
            } else {
                crate::terminal_notifier::NotificationContext::Complete
            };
            // A failed or interrupted turn finalizes the open live group; a
            // successful one leaves it spinning.
            if matches!(status, Some("failed" | "interrupted")) {
                app.view.transcript.finalize_tool_group();
            }
            app.view.transcript.settle_reasoning();
            app.view.transcript.settle_compactions(None);
            app.session.active_turn_id = None;
            // The retried turn ended; its merged row is final until a snapshot replaces it.
            app.session.retry_continuation = None;
            if retry::auto_continue_incomplete_stream(app, client, &turn) {
                return true;
            }
            let retryable = turn
                .failure()
                .is_some_and(|code| RETRYABLE_ERROR_CODES.contains(&code));
            app.session.can_retry = retryable;
            if let Some((message, hint)) = turn_error_message(app, &turn, retryable) {
                app.terminal_notifier.set_error_message(&message);
                let id = new_message_id();
                local::add_command_error(
                    &mut app.view.transcript,
                    &id,
                    &format!("{message}{hint}"),
                );
                if retryable {
                    retry::offer(app, Some(id));
                }
            } else if retryable {
                retry::offer(app, None);
            }
            if !retryable {
                retry::cancel(app);
            }
            app.set_status(Status::Ready);
            // A failure feeds the summary its error, unless it auto-retries
            // (`incomplete_stream` with no queued work); an interrupt drops the
            // data entirely; then the summary is requested.
            let interrupted = match status {
                Some("failed") if !turn_summary::retries_incomplete_stream(app, &turn) => {
                    turn_summary::on_turn_error(app, &turn_summary::turn_error_message(&turn));
                    false
                }
                Some("interrupted") => {
                    turn_summary::on_turn_cancel(app);
                    true
                }
                _ => false,
            };
            turn_summary::on_turn_end(app, client);
            if !interrupted && !app.queue.is_empty() && !app.queue.paused {
                app.session.deferred_completion = turn
                    .queue_item_id
                    .filter(|id| app.queue.promotion_pending(id))
                    .map(|id| (id, context));
            } else if app.approval.open || app.question_app.open {
                app.terminal_notifier.remember_completion(if interrupted {
                    crate::program_status::ProgramState::Idle
                } else {
                    context.program_state()
                });
            } else if interrupted {
                app.terminal_notifier
                    .remember_completion(crate::program_status::ProgramState::Idle);
            } else {
                app.terminal_notifier
                    .notify(context, std::time::Instant::now());
            }
        }
        notification::TURN_QUEUE_UPDATED => {
            if let Some(queue) = event.params.get("queue") {
                message_queue::sync(app, queue);
            }
        }
        notification::SESSION_STATS_UPDATED => {
            let stats: crate::server::SessionStats =
                crate::utils::json::read(&event.params["stats"]);
            let current = stats.context_tokens;
            let max = event.params["contextWindow"].as_u64().unwrap_or(0);
            // The context progress masks the session budget with
            // the viewed child's own context usage while a subagent is viewed.
            app.subagents.main_tokens = (current, max);
            if app.subagents.viewed_subagent_id.is_none() {
                app.session.tokens = (current, max);
            }
            app.session.stats = stats;
        }
        notification::ARTIFACTS_CREATED | notification::ARTIFACTS_UPDATED => {
            if let Some(artifact) = event.params.get("artifact") {
                if let Ok(artifact) = crate::server::PublicSessionArtifact::deserialize(artifact) {
                    artifacts::upsert(app, artifact);
                }
            }
        }
        notification::SESSION_COMPACTED => {
            // The handoff installs the replacement session.
            match compact::compacted_handoff(&event.params) {
                Some((id, child_sessions)) => compact::apply_compacted(app, id, child_sessions),
                None => compact::settle_compact(app),
            }
        }
        // A child session update upserts the child, announces the ready
        // transition, and coalesce the viewed child's transcript refresh.
        notification::CHILD_SESSION_UPDATED => {
            if let Ok(params) = crate::server::ChildSessionUpdatedParams::deserialize(&event.params)
            {
                let session = params.child_session.clone();
                app.subagents.replace_child_session(params.child_session);
                let announced = app
                    .subagents
                    .transcripts
                    .announce_ready_transition(&session);
                if app.subagents.viewed_subagent_id.as_deref() == Some(session.id.as_str()) {
                    if announced {
                        subagents::anchor(app);
                    }
                    subagents::refresh_context_progress(app);
                    subagents::schedule_refresh(app);
                }
                subagents::refresh(app);
            }
        }
        // A cleared context resets the subagent views; the
        // handoff state carries the replacement session's children. The
        // replacement is the live session from here on, or every later
        // notification reads as foreign (`apply_compacted` parity).
        notification::SESSION_CONTEXT_CLEARED => {
            if let Some(state) = event.params.get("state").cloned().and_then(|state| {
                serde_json::from_value::<crate::server::PublicSessionState>(state).ok()
            }) {
                app.set_session_id(state.session.id.clone());
                app.subagents.seed_snapshot(state.child_sessions.clone());
                subagents::reset_views(app);
                subagents::refresh(app);
            }
        }
        // Server-pushed operational warning/error: surface as a toast.
        notification::WARNING => {
            if let Some(message) = event
                .params
                .pointer("/warning/message")
                .and_then(Value::as_str)
            {
                let seconds = match event
                    .params
                    .pointer("/warning/code")
                    .and_then(Value::as_str)
                {
                    Some(INTEGRATIONS_UNAVAILABLE) => mcp::INTEGRATIONS_UNAVAILABLE_TOAST_SECS,
                    _ => SERVER_TOAST_SECS,
                };
                app.show_toast(message.to_owned(), ToastSeverity::Warning, seconds);
            }
        }
        notification::ERROR => {
            if let Some(message) = event.params["error"]["message"].as_str() {
                app.show_toast(message.to_owned(), ToastSeverity::Error, SERVER_TOAST_SECS);
            }
        }
        // ADR 0009: `turn/retrying` is a compatibility notification. New reducers
        // ignore it and derive the retry label from `PublicSessionState.retrying`
        // via `sync_retrying_label` on `session/snapshot`.
        notification::TURN_RETRYING => {}
        // Narration cancels only on a narrator config change; a plain runtime
        // refresh (after resume, turn/completed, ...) does not.
        notification::RUNTIME_UPDATED => {
            apply_runtime_value(app, &event.params);
            config_issues::show_updated_config_warnings(app, &event.params);
        }
        // Forwarded by the client: a callback the user must answer.
        server_method::CALLBACK_CALL => {
            if event.params["callback"]["detail"]["kind"] == "user_input" {
                return question_app::on_callback_call(app, &event.params);
            }
        }
        // `/mcp login` streams the OAuth URL: show it and open the browser.
        notification::MCP_AUTH_URL => {
            let Some(url) = event.params["url"].as_str() else {
                return true;
            };
            // The OAuth bottom-app owns the URL of the login it started; only
            // `/mcp login` prints it and opens the browser itself.
            if app.mcp_oauth.open {
                crate::mcp_oauth::on_auth_url(app, url);
                return true;
            }
            local::add_command_warning(
                &mut app.view.transcript,
                &submission::new_message_id(),
                &format!("Open this URL in your browser:\n\n  {url}"),
            );
            crate::external_url::open(url);
        }
        // The catalog publishes it alongside the canonical one; swallowing it
        // keeps the URL from being handled twice.
        notification::MCP_AUTH_URL_LEGACY => {}
        notification::TELEPORT_EVENT => crate::teleport::on_event(app, client, &event.params),
        _ => {}
    }
    true
}

fn entry_finishes_reasoning(entry: &EntryHead) -> bool {
    match entry.entry_type() {
        Some("message") => entry.role.as_deref() == Some("assistant"),
        Some("effect" | "callback") => true,
        Some("checkpoint") => entry.kind.as_deref() == Some("compaction"),
        _ => false,
    }
}

/// Arm `/retry` only on these server-side errors.
const RETRYABLE_ERROR_CODES: &[&str] = &[
    "backend_error",
    "rate_limit",
    "response_too_long",
    "incomplete_stream",
];

/// The error text shown
/// in the transcript on a failed turn. Returns `(message, hint)` where hint
/// is the `/retry` suffix for retryable errors.
fn turn_error_message(app: &App, turn: &PublicTurn, retryable: bool) -> Option<(String, String)> {
    if turn.status() != Some("failed") {
        return None;
    }
    let message = match turn.error_code() {
        Some("rate_limit") => rate_limit_message(app.whoami.account()),
        _ => turn.error_message().unwrap_or("Turn failed").to_owned(),
    };
    let hint = if retryable {
        "\n\nRun /retry [additional instructions] to continue the interrupted response.".to_owned()
    } else {
        String::new()
    };
    Some((message, hint))
}

/// The friendly rate-limit text, with the
/// upgrade line only when the account exposes a rate-limit action.
pub fn rate_limit_message(account: &Value) -> String {
    if account
        .pointer("/rateLimitAction")
        .is_some_and(|action| !action.is_null())
    {
        return "Rate limits exceeded. Please wait a moment before trying again, \
            or upgrade to Pro for higher rate limits and uninterrupted access."
            .to_owned();
    }
    "Rate limits exceeded. Please wait a moment before trying again.".to_owned()
}
