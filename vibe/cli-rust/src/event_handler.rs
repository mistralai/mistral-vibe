//! Reduce startup and app-server events into UI state.

use std::sync::Arc;

use crate::server::{notification, server_method};
use crate::server::{Client, Notification};
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
    agents, approval, completion_manager, config, config_issues, feedback, mcp, message_queue,
    model_picker, observability, post_ready, question_app, resume_picker, session_exit, startup,
    subagents, todo_tracker, turn_summary, ui,
};

/// Seconds a server warning/error toast stays up (Python `App.notify` default).
const SERVER_TOAST_SECS: u64 = 5;

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
                config::load(session_id, client, config_tx);
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
            // The wizard is a pre-session surface (Python `run_onboarding`):
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
            app.set_status(Status::Failed);
            false
        }
    }
}

/// Python `_auto_resume_on_startup`'s opening line: `--continue`/`--resume <id>`
/// say so on the first frame, while the handshake attaches that session.
pub fn announce_resume(app: &mut App) {
    if matches!(
        app.session.startup_resume,
        StartupResume::ById(_) | StartupResume::Continue
    ) {
        local::add_command_result(
            &mut app.view.transcript,
            &submission::new_message_id(),
            "Resuming session\u{2026}",
        );
    }
}

/// Python `_show_dangerous_directory_warning`: the borderless warning mounted
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

/// Python `_schedule_update_notification`/`_check_update`: one detached,
/// non-forced check after startup, its outcome only written to the update
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
            // Python warns for `UpdateError`; a failed cache write never
            // reaches here — it is logged inside, like Python's swallowed
            // `OSError`.
            tracing::warn!("Update check failed: {}", error.message)
        }
    });
}

/// Python `_auto_resume_on_startup`'s closing line: what the attach did.
fn attach_notice(app: &mut App, attach: startup::Attach) {
    let id = submission::new_message_id();
    let transcript = &mut app.view.transcript;
    match attach {
        startup::Attach::Started => {}
        startup::Attach::Resumed(session) => local::add_command_result(
            transcript,
            &id,
            &format!("Resumed session `{}`", resume_picker::short_id(&session)),
        ),
        startup::Attach::NoPrevious => {
            local::add_command_result(transcript, &id, "No previous sessions found.")
        }
        startup::Attach::Failed(message) => local::add_command_error(transcript, &id, &message),
    }
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
        let title = runtime
            .pointer("/sessionLog/title")
            .and_then(Value::as_str)
            .or(state.session.title.as_deref())
            .unwrap_or("");
        app.terminal_notifier.set_default_title(title);
    }
    app.subagents.status_list_enabled = startup::read_show_subagent_status_list(&runtime);
    app.quit.ask_confirmation_on_exit = startup::read_ask_confirmation_on_exit(&runtime);
    apply_final_startup_config(app, startup_cache);
    app.view
        .transcript
        .set_show_reasoning(startup::read_show_thinking_nodes(&runtime));
    message_queue::reconcile_snapshot(app, client, &state);
    // The picker can resume before the handshake finishes, and that session
    // wins: the one started here is only what Esc would fall back to.
    if !app.session.resumed {
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
    apply_stats(
        app,
        runtime.pointer("/runtime/stats").unwrap_or(&Value::Null),
    );
    // This run's `--worktree` verdict (the announce), pinned apart from the
    // session's own tracking: a resumed session's restored effect carries the
    // original run's `created`, and reuse must stay non-cleaning (Python's
    // `worktree_session.created`).
    app.session.prepared_worktree = prepared;
    // The notifications the settle wait absorbed, applied once the state they
    // follow exists, so the footer lands on the worktree before first paint.
    for notification in absorbed {
        if !apply_notification(app, client, &notification) {
            tracing::debug!("absorbed notification deferred; no approval is pending pre-TUI");
        }
    }
    // Python chdirs before SessionOptions, and later picker resumes resend
    // this config, so it must keep naming the settled worktree, not the
    // checkout the run started in.
    if let Some(cwd) = settled {
        app.session.agent_config.cwd = Some(cwd);
    }
    // Python snapshots the usage baseline once the session attaches; a picker
    // resume that already adopted keeps the baseline it set.
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
    // Python reads the what's-new gate inside mount, then schedules the
    // update check; keeping the spawn after mount preserves that ordering.
    // ADR 0015: apply the live trust policy before the check's gateway
    // builds its client.
    crate::update_notifier::gateway::configure_tls_trust(config.enable_system_trust_store);
    spawn_background_update_check(config.enable_update_checks);
    post_ready::fetch(app, client, config.show_greeting);
    completion_manager::refresh(app);
    app.set_status(Status::Ready);
    approval::show_pending(app);
    record_startup(app, &attach, is_cold_start, config.harness_selection_source);
    attach_notice(app, attach);
    crate::commands::shell::flush_pending(app, client);
    message_queue::flush_pending(app, client);
    // The resume picker owns the startup: the prompt waits for the session the
    // user picks, or is dropped when they cancel (Python returns before
    // `_process_startup_prompt` when the picker is up).
    if !app.resume_picker.open {
        process_initial_prompt(app, client);
    }
    // Prompts flush before deferred slash commands; a prompt-then-command typed
    // during Starting thus dispatches in that order regardless of submission
    // order. Python preserves true order via one gated dispatch.
    submission::flush_pending(app, client, config_tx)
}

/// Python `_send_startup_telemetry_once`. Fields this client has no surface for
/// (teleport) are honestly `false`; `session_init_duration_ms` stays null since
/// the server does not report it to this client.
fn record_startup(
    app: &mut App,
    attach: &startup::Attach,
    is_cold_start: bool,
    harness_selection_source: Option<String>,
) {
    let properties = crate::telemetry::bmap(serde_json::json!({
        "agent_ready_duration_ms": startup::agent_ready_duration_ms(),
        "session_init_duration_ms": Value::Null,
        "has_initial_prompt": app.session.initial_prompt.as_deref().is_some_and(|p| !p.is_empty()),
        "teleport_on_start": false,
        "show_resume_picker": matches!(app.session.startup_resume, StartupResume::Picker),
        "is_resuming_session": matches!(attach, startup::Attach::Resumed(_)),
        "prompt_for_workspace_trust": app.session.trust_prompted,
        "is_cold_start": is_cold_start,
        "harness_selection_source": harness_selection_source,
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

/// Python `_process_initial_prompt`: send the positional `vibe <prompt>` argument
/// into the session, once as a plain prompt like `_handle_user_message` does, or
/// teleport it under `--teleport`.
pub fn process_initial_prompt(app: &mut App, client: &Arc<Client>) {
    // Python's `args.initial_prompt or stdin_prompt` drops an empty string.
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
    // Python never re-checks the custom-tools deprecation on a plain runtime
    // refresh, only after the initial history and a transcript rebuild.
    app.session.runtime = runtime.clone();
    apply_stats(
        app,
        runtime.pointer("/runtime/stats").unwrap_or(&Value::Null),
    );
    completion_manager::refresh(app);
    mcp::apply_runtime(app, runtime);
}

/// Apply a mutation response's `runtime` when it carries one (Python
/// `if response.runtime is not None: state.apply_runtime(response.runtime)`).
pub fn apply_response_runtime(app: &mut App, response: &Value) {
    if response.get("runtime").is_some_and(Value::is_object) {
        apply_runtime_value(app, response);
    }
}

fn apply_stats(app: &mut App, stats: &Value) {
    app.session.stats.steps = stats.get("steps").and_then(Value::as_u64).unwrap_or(0);
    app.session.stats.session_prompt_tokens = stats
        .get("sessionPromptTokens")
        .and_then(Value::as_u64)
        .unwrap_or(0);
    app.session.stats.session_completion_tokens = stats
        .get("sessionCompletionTokens")
        .and_then(Value::as_u64)
        .unwrap_or(0);
    app.session.stats.session_cached_tokens = stats
        .get("sessionCachedTokens")
        .and_then(Value::as_u64)
        .unwrap_or(0);
    app.session.stats.last_turn_total_tokens = stats
        .get("lastTurnPromptTokens")
        .and_then(Value::as_u64)
        .unwrap_or(0)
        + stats
            .get("lastTurnCompletionTokens")
            .and_then(Value::as_u64)
            .unwrap_or(0);
    app.session.stats.last_turn_cached_tokens = stats
        .get("lastTurnCachedTokens")
        .and_then(Value::as_u64)
        .unwrap_or(0);
    let input_price = stats
        .get("inputPricePerMillion")
        .and_then(Value::as_f64)
        .unwrap_or(0.0);
    let output_price = stats
        .get("outputPricePerMillion")
        .and_then(Value::as_f64)
        .unwrap_or(0.0);
    let cached_price = stats
        .get("cachedInputPricePerMillion")
        .and_then(Value::as_f64)
        .unwrap_or(input_price);
    app.session.stats.session_cost =
        (app.session
            .stats
            .session_prompt_tokens
            .saturating_sub(app.session.stats.session_cached_tokens) as f64
            * input_price
            + app.session.stats.session_cached_tokens as f64 * cached_price
            + app.session.stats.session_completion_tokens as f64 * output_price)
            / 1_000_000.0;
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

/// Python mounts a live reasoning row with the bulk fold state
/// (`ReasoningMessage(collapsed=get_tools_collapsed())`); other live widgets
/// keep their constructor's collapsed default.
fn expand_live_reasoning(app: &mut App, params: &Value) {
    if app.view.tools_collapsed {
        return;
    }
    let Some(id) = params.pointer("/entry/id").and_then(Value::as_str) else {
        return;
    };
    if app.view.transcript.is_reasoning(id) {
        app.view.expanded.insert(id.to_owned());
        app.view.transcript_cache.invalidate_layouts();
    }
}

/// Python `_record_todos`: a settled todo effect replaces the pinned list.
fn record_todos(app: &mut App, raw: Option<&Value>) {
    let Some(todos) = raw.and_then(todo_tracker::todos_from_entry) else {
        return;
    };
    app.todo_tracker.record(todos);
}

/// The stored entry an update points at, cloned out before `record_todos` takes `&mut app`.
fn updated_entry_raw(app: &App, params: &Value) -> Option<Value> {
    params
        .get("entryId")
        .and_then(Value::as_str)
        .and_then(|id| app.view.transcript.entry_raw(id))
        .cloned()
}

fn sync_loading_label(app: &mut App, params: &Value) {
    if !matches!(app.session.status, Status::Generating { .. }) {
        return;
    }
    match params.pointer("/entry/type").and_then(Value::as_str) {
        Some("reasoning") => {
            app.view
                .loading
                .set_label(ui::loading::THINKING_LOADING_STATUS);
        }
        // Python's hook notices steer the loading status; other detail kinds
        // leave it untouched, so nothing else resets it here.
        Some("notice") => match params.pointer("/entry/detail/kind").and_then(Value::as_str) {
            Some("hook_started") => {
                let name = params
                    .pointer("/entry/detail/hookName")
                    .and_then(Value::as_str)
                    .unwrap_or("");
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
        // Python starts a call by showing its `statusText`, and resets to the
        // default once a settled result lands — but only when no other call is
        // live (`LoadingWidget.set_status`, `_handle_effect_completed`).
        Some("effect") => {
            let id = params
                .pointer("/entry/id")
                .and_then(Value::as_str)
                .unwrap_or("");
            let running = params
                .pointer("/entry/generationStatus")
                .and_then(Value::as_str)
                == Some("in_progress");
            if running {
                app.view.loading.set_label(
                    params
                        .pointer("/entry/detail/display/statusText")
                        .and_then(Value::as_str)
                        .unwrap_or(""),
                );
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
            let id = params.get("entryId").and_then(Value::as_str).unwrap_or("");
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

/// Python `LoadingWidget.set_retrying`: a snapshot with `retrying` set swaps
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
    ) {
        return false;
    }
    let Some(session_id) = event.params.get("sessionId").and_then(Value::as_str) else {
        return false;
    };
    app.session
        .session_id
        .as_deref()
        .is_some_and(|current| current != session_id)
}

/// Apply one notification, or return `false` when the event loop must retain it.
pub fn apply_notification(app: &mut App, client: &Arc<Client>, event: &Notification) -> bool {
    if event.method == server_method::CALLBACK_CALL
        && event
            .params
            .pointer("/callback/detail/kind")
            .and_then(Value::as_str)
            == Some("approval")
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
    // Warnings/errors are transient toasts, never re-sent in the snapshot: surface them pre-Ready like Python's out-of-band listener.
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
            if event
                .params
                .pointer("/entry/detail/kind")
                .and_then(Value::as_str)
                == Some("session_title_updated")
            {
                if let Some(title) = event
                    .params
                    .pointer("/entry/detail/title")
                    .and_then(Value::as_str)
                {
                    app.terminal_notifier.set_default_title(title);
                }
            }
            if let Some(entry_id) = event.params.pointer("/entry/id").and_then(Value::as_str) {
                crate::commands::shell::apply_started(app, client, entry_id);
            }
            if entry_finishes_reasoning(&event.params) {
                app.view.transcript.settle_reasoning();
            }
            message_queue::steering_history_added(app, client, &event.params);
            retry::on_entry_added(app, &event.params);
            app.view.transcript.add(&event.params);
            crate::worktree::track_entry(app, &event.params);
            record_todos(app, event.params.get("entry"));
            retry_continuation::merge_continuation(app);
            // Python sets `_turn_assistant_message` inside the resolve, so a
            // continuation's hidden entry maps to the merged row it feeds.
            retry::track_turn_assistant(app, &event.params);
            expand_live_reasoning(app, &event.params);
            sync_loading_label(app, &event.params);
            turn_summary::track_narrator_added(app, &event.params);
            if let Some(entry) = event.params.get("entry") {
                subagents::remember_parent_instruction_from_entry(app, entry);
            }
        }
        notification::HISTORY_ENTRY_UPDATED => {
            app.view.transcript.update(&event.params);
            crate::worktree::track_entry(app, &event.params);
            record_todos(app, updated_entry_raw(app, &event.params).as_ref());
            retry_continuation::merge_continuation(app);
            sync_loading_label(app, &event.params);
            turn_summary::track_narrator_updated(app, &event.params);
            // The wire patch carries no entry; the stored one is post-patch.
            if let Some(id) = event.params.get("entryId").and_then(Value::as_str) {
                let entry = app.view.transcript.entry_raw(id).cloned();
                if let Some(entry) = entry {
                    subagents::remember_parent_instruction_from_entry(app, &entry);
                }
            }
        }
        notification::SESSION_SNAPSHOT => {
            if let Some(state) = event.params.get("state") {
                if let Ok(state) =
                    serde_json::from_value::<crate::server::PublicSessionState>(state.clone())
                {
                    message_queue::reconcile_snapshot(app, client, &state);
                    // ADR 0009: a live same-session snapshot retains the loaded
                    // contiguous prefix and merges the page into it (Python's
                    // `SessionSnapshot` arm only touches the loading label); a
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
                    subagents::refresh(app);
                }
            }
        }
        notification::TURN_STARTED => {
            app.session.active_turn_id = event
                .params
                .pointer("/turn/id")
                .and_then(Value::as_str)
                .map(str::to_owned);
            let queue_item_id = event
                .params
                .pointer("/turn/queueItemId")
                .and_then(Value::as_str);
            let interrupt = message_queue::take_interrupt_on_start(app);
            if let Some(queue_item_id) = queue_item_id {
                if message_queue::turn_started(app, client, queue_item_id) {
                    feedback::maybe_show(app, client, app.session.session_id.clone());
                }
            }
            app.set_status(Status::Generating {
                since: std::time::Instant::now(),
            });
            // Python `_begin_unsolicited_turn`: end the previous turn's open
            // data, drop whatever summary it still had in flight, and open a
            // fresh one.
            turn_summary::on_turn_end(app, client);
            turn_summary::cancel(app);
            turn_summary::on_turn_start(app, "");
            // The user interrupted this turn before it started (Python `_interrupt_turn`).
            if interrupt {
                app.set_status(Status::Ready);
                submission::send_interrupt(app, client);
            }
        }
        // The server promotes the next queued prompt itself; the loading area goes
        // idle until its `turn/started` arrives, as Python's does.
        notification::TURN_COMPLETED => {
            // Python `_complete_unsolicited_turn`: a failed or interrupted turn
            // finalizes the open live group (`stop_current_tool_call`); a
            // successful one leaves it spinning.
            if matches!(
                event.params.pointer("/turn/status").and_then(Value::as_str),
                Some("failed" | "interrupted")
            ) {
                app.view.transcript.finalize_tool_group();
            }
            app.view.transcript.settle_reasoning();
            app.session.active_turn_id = None;
            // The retried turn ended; its merged row is final until a snapshot replaces it.
            app.session.retry_continuation = None;
            if retry::auto_continue_incomplete_stream(app, client, &event.params) {
                return true;
            }
            let retryable = is_retryable_turn_error(&event.params);
            app.session.can_retry = retryable;
            if let Some((message, hint)) = turn_error_message(app, &event.params, retryable) {
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
            // Python `_complete_unsolicited_turn`: a failure feeds the summary
            // its error — unless it auto-retries (`incomplete_stream` with no
            // queued work) — an interrupt drops the data entirely; then
            // `_finalize_turn_ui` requests the summary.
            match event.params.pointer("/turn/status").and_then(Value::as_str) {
                Some("failed") if !turn_summary::retries_incomplete_stream(app, &event.params) => {
                    turn_summary::on_turn_error(
                        app,
                        &turn_summary::turn_error_message(&event.params),
                    )
                }
                Some("interrupted") => turn_summary::on_turn_cancel(app),
                _ => {}
            }
            turn_summary::on_turn_end(app, client);
            app.terminal_notifier.notify(
                crate::terminal_notifier::NotificationContext::Complete,
                std::time::Instant::now(),
            );
        }
        notification::TURN_QUEUE_UPDATED => {
            if let Some(queue) = event.params.get("queue") {
                message_queue::sync(app, queue);
            }
        }
        notification::SESSION_STATS_UPDATED => {
            let current = event
                .params
                .pointer("/stats/contextTokens")
                .and_then(Value::as_u64)
                .unwrap_or(0);
            let max = event
                .params
                .get("contextWindow")
                .and_then(Value::as_u64)
                .unwrap_or(0);
            // Python `_update_context_progress` masks the session budget with
            // the viewed child's own context usage while a subagent is viewed.
            app.subagents.main_tokens = (current, max);
            if app.subagents.viewed_subagent_id.is_none() {
                app.session.tokens = (current, max);
            }
            apply_stats(app, event.params.get("stats").unwrap_or(&Value::Null));
        }
        notification::SESSION_COMPACTED => {
            // Python `replace_state`: the handoff installs the replacement session.
            match compact::compacted_handoff(&event.params) {
                Some((id, child_sessions)) => compact::apply_compacted(app, id, child_sessions),
                None => compact::settle_compact(app),
            }
        }
        // Python `ChildSessionUpdated`: upsert the child, announce the ready
        // transition, and coalesce the viewed child's transcript refresh.
        notification::CHILD_SESSION_UPDATED => {
            if let Ok(params) = serde_json::from_value::<crate::server::ChildSessionUpdatedParams>(
                event.params.clone(),
            ) {
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
        // Python `SessionContextCleared` -> `_reset_subagent_views`; the
        // handoff state carries the replacement session's children. The
        // replacement is the live session from here on, or every later
        // notification reads as foreign (`apply_compacted` parity).
        notification::SESSION_CONTEXT_CLEARED => {
            if let Some(state) = event.params.get("state").cloned().and_then(|state| {
                serde_json::from_value::<crate::server::PublicSessionState>(state).ok()
            }) {
                app.set_session_id(state.session.id.clone());
                subagents::reset_views(app);
                app.subagents.seed_snapshot(state.child_sessions.clone());
                subagents::refresh(app);
            }
        }
        // Server-pushed operational warning/error: surface as a toast, like
        // Python's `ServerWarning`/`ServerError` -> `App.notify`.
        notification::WARNING => {
            if let Some(message) = event
                .params
                .pointer("/warning/message")
                .and_then(Value::as_str)
            {
                app.show_toast(
                    message.to_owned(),
                    ToastSeverity::Warning,
                    SERVER_TOAST_SECS,
                );
            }
        }
        notification::ERROR => {
            if let Some(message) = event
                .params
                .pointer("/error/message")
                .and_then(Value::as_str)
            {
                app.show_toast(message.to_owned(), ToastSeverity::Error, SERVER_TOAST_SECS);
            }
        }
        // ADR 0009: `turn/retrying` is a compatibility notification. New reducers
        // ignore it and derive the retry label from `PublicSessionState.retrying`
        // via `sync_retrying_label` on `session/snapshot`.
        notification::TURN_RETRYING => {}
        // Python `NarratorManager.sync` cancels only on a narrator config
        // change; a plain runtime refresh (after resume, turn/completed, ...)
        // does not.
        notification::RUNTIME_UPDATED => {
            apply_runtime_value(app, &event.params);
        }
        // Forwarded by the client: a callback the user must answer.
        server_method::CALLBACK_CALL => {
            if event
                .params
                .pointer("/callback/detail/kind")
                .and_then(Value::as_str)
                == Some("user_input")
            {
                question_app::on_callback_call(app, &event.params);
            }
        }
        // `/mcp login` streams the OAuth URL: show it and open the browser.
        notification::MCP_AUTH_URL => {
            let Some(url) = event.params.get("url").and_then(Value::as_str) else {
                return true;
            };
            // The OAuth bottom-app owns the URL of the login it started; only
            // `/mcp login` prints it and opens the browser itself.
            if app.mcp_oauth.open {
                crate::mcp_oauth::on_auth_url(app, url);
                return true;
            }
            local::add_command_result(
                &mut app.view.transcript,
                &submission::new_message_id(),
                &format!("Open this URL in your browser:\n\n  {url}"),
            );
            crate::external_url::open(url);
        }
        // The catalog publishes it alongside the canonical one; swallowing it
        // keeps the URL from being handled twice (Python `consume_notification`).
        notification::MCP_AUTH_URL_LEGACY => {}
        notification::TELEPORT_EVENT => crate::teleport::on_event(app, client, &event.params),
        _ => {}
    }
    true
}

fn entry_finishes_reasoning(params: &Value) -> bool {
    match params.pointer("/entry/type").and_then(Value::as_str) {
        Some("message") => {
            params.pointer("/entry/role").and_then(Value::as_str) == Some("assistant")
        }
        Some("effect" | "callback") => true,
        Some("checkpoint") => {
            params.pointer("/entry/kind").and_then(Value::as_str) == Some("compaction")
        }
        _ => false,
    }
}

/// Python `_RETRYABLE_TURN_ERROR_CODES`: arm `/retry` only on these server-side errors.
const RETRYABLE_ERROR_CODES: &[&str] = &[
    "backend_error",
    "rate_limit",
    "response_too_long",
    "incomplete_stream",
];

/// Whether a `turn/completed` notification carries a retryable failure,
/// matching Python's `offer_retry` gate in `_mount_turn_error`.
fn is_retryable_turn_error(params: &Value) -> bool {
    let status = params.pointer("/turn/status").and_then(Value::as_str);
    if status != Some("failed") {
        return false;
    }
    let Some(code) = params.pointer("/turn/error/code").and_then(Value::as_str) else {
        return false;
    };
    RETRYABLE_ERROR_CODES.contains(&code)
}

/// Python `_resolve_turn_error_message` + `_retry_hint`: the error text shown
/// in the transcript on a failed turn. Returns `(message, hint)` where hint
/// is the `/retry` suffix for retryable errors.
fn turn_error_message(app: &App, params: &Value, retryable: bool) -> Option<(String, String)> {
    let status = params.pointer("/turn/status").and_then(Value::as_str)?;
    if status != "failed" {
        return None;
    }
    let message = match params.pointer("/turn/error/code").and_then(Value::as_str) {
        Some("rate_limit") => rate_limit_message(app.whoami.account()),
        _ => params
            .pointer("/turn/error/message")
            .and_then(Value::as_str)
            .unwrap_or("Turn failed")
            .to_owned(),
    };
    let hint = if retryable {
        "\n\nRun /retry [additional instructions] to continue the interrupted response.".to_owned()
    } else {
        String::new()
    };
    Some((message, hint))
}

/// Python `_rate_limit_message`: the friendly rate-limit text, with the
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
