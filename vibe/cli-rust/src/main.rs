//! vibe-rs: a render-perf PoC TUI for Vibe over the app-server JSON-RPC protocol.

use std::sync::Arc;

use anyhow::{Context, Result};
use clap::Parser;
use tokio::sync::mpsc;

use vibe_rs::app::App;
use vibe_rs::cli::{workdir_to_string, Cli};
use vibe_rs::commands::CommandEvent;
use vibe_rs::event_loop::{
    APPROVAL_CHANNEL_CAP, COMMAND_CHANNEL_CAP, FEEDBACK_CHANNEL_CAP, NARRATOR_CHANNEL_CAP,
    QUEUE_CHANNEL_CAP, VOICE_CHANNEL_CAP,
};
use vibe_rs::headless;
use vibe_rs::headless_prompt;
use vibe_rs::input_thread::InputThread;
use vibe_rs::message_queue::QueueEvent;
use vibe_rs::observability::logging;
use vibe_rs::paste_image::CHANNEL_CAP as PASTE_IMAGE_CHANNEL_CAP;
use vibe_rs::server::Client;
use vibe_rs::startup::{handshake, launch_from_env, StartupEvent, StartupRecorder};
use vibe_rs::voice::VoiceEvent;
use vibe_rs::{
    agents, approval, config, connector_auth, event_loop, feedback, log_level_picker, mcp,
    mcp_oauth, model_picker, observability, resume_picker, rewind, session_exit, terminal,
    theme_picker, thinking_picker, turn_summary, ui, utils,
};

#[tokio::main(flavor = "current_thread")]
async fn main() -> std::process::ExitCode {
    match run().await {
        Ok(code) => code,
        Err(error) => {
            session_exit::print_error(&error.to_string());
            // Python flushes in a `finally` on every exit path, after the
            // error itself is reported.
            observability::sentry::flush();
            std::process::ExitCode::from(1)
        }
    }
}

async fn run() -> Result<std::process::ExitCode> {
    let mut timings = StartupRecorder::new();
    timings.record("process_entry");
    let cli = Cli::parse();
    let invocation_cwd = std::env::current_dir().ok();
    let startup_resume = cli.startup_resume();
    let cwd = cli.change_workdir().unwrap_or_else(|error| {
        eprintln!("{error:#}");
        std::process::exit(1);
    });
    let cwd_text = workdir_to_string(&cwd)?;
    let launch = launch_from_env(invocation_cwd.as_deref().unwrap_or(&cwd));
    let cwd = Some(cwd);
    timings.record("cli_parsed");
    logging::init_file_logging(logging::log_file().as_deref());
    observability::sentry::install_panic_hook();

    // Python reads piped stdin once, before choosing interactive vs headless, so
    // `echo hi | vibe` feeds the TUI initial prompt while /dev/tty (which
    // crossterm reads for events) keeps terminal input interactive.
    let stdin_prompt = headless_prompt::read_stdin_prompt().await;

    // Headless (programmatic) mode: no TUI, no terminal init.
    if cli.is_headless() {
        let options = match cli.headless_options(stdin_prompt) {
            Some(opts) => opts,
            None => {
                eprintln!("Error: No prompt provided for programmatic mode");
                std::process::exit(1);
            }
        };
        // `change_workdir` already resolved and chdir'd; re-resolving here would
        // reinterpret relative `--workdir` values against the new directory.
        observability::sentry::init_sentry(false, true, [].into());
        let result = headless::run(options, Some(cwd_text), launch).await;
        observability::sentry::flush();
        // Python cli.py prints `ProgrammaticLimitError` bare on stderr, exit 1; handling it
        // after `run` returns keeps session/stop and the kill_on_drop teardown in the path.
        if let Err(err) = &result {
            if err.downcast_ref::<headless::LimitError>().is_some() {
                eprintln!("{err}");
                std::process::exit(1);
            }
        }
        // Headless success is a plain exit 0; errors take `run`'s Err path.
        return result.map(|()| std::process::ExitCode::SUCCESS);
    }

    // No local config read exists here, so the `enableTelemetry` gate is
    // unknowable before Ready: `SENTRY_DSN` is the operator's opt-in that
    // covers the spawn/handshake window. Ready reconfigures per config.
    observability::sentry::init_pre_ready(
        false,
        [("entrypoint".to_owned(), "vibe-rs".to_owned())].into(),
    );

    // Resolve the shared session flags before the TUI owns the terminal, so an
    // invalid `--add-dir` fails with a readable error instead of a crash later.
    let interactive_agent_config = cli.interactive_agent_config(Some(cwd_text.clone()))?;

    let cached_startup_config = utils::startup_cache::StartupConfig::load();
    let show_unready_config = cached_startup_config.is_none();
    let startup_config = cached_startup_config.unwrap_or_default();
    let resolved_auto = vibe_rs::theme_detection::resolve_auto_theme();
    ui::theme::prepare_active(&startup_config.theme, resolved_auto);
    // Install before the TUI owns the terminal, so a failure exits cleanly.
    let shutdown = vibe_rs::server::signal::install().context("install signal handlers")?;
    let (mut terminal, terminal_guard) = terminal::init();
    timings.record("terminal_ready");

    let (files, file_changes) = utils::file_index::FileIndex::start(cwd.clone());
    let cached_tokens = (0, startup_config.context_window);
    let mut app = App::default();
    // Hold the positional prompt until startup converges (Python `_initial_prompt`).
    // Python falls back to piped stdin: `initial_prompt or stdin_prompt`, where an
    // empty positional (`vibe ""`) is falsy and yields to the piped prompt.
    app.session.initial_prompt = cli.interactive_initial_prompt(stdin_prompt);
    // Python keeps one `_session_options` for the process and resends it on every resume.
    app.session.agent_config = interactive_agent_config.clone();
    app.chat_input.history = utils::history_manager::HistoryManager::load();
    let history_flush = app.chat_input.history.flush_handle();
    app.session.cwd = Some(cwd_text);
    app.session.startup_resume = startup_resume;
    app.completion.files = files;
    app.completion.skills = startup_config.skills.clone();
    agents::show_startup_agents(&mut app, &startup_config);
    app.session.startup_config = startup_config;
    app.session.tokens = cached_tokens;
    vibe_rs::event_handler::announce_resume(&mut app);
    vibe_rs::event_handler::show_dangerous_directory_warning(&mut app);
    terminal.draw(|frame| app.draw(frame))?;
    timings.record("first_draw");

    let (client, child, notifications, crash_rx) = match Client::spawn(launch).await {
        Ok(spawned) => spawned,
        Err(error) => {
            // The Sentry bridge only carries ERROR records, so the startup
            // window's own failure sites log their own fatal errors.
            tracing::error!(vibe_boundary = "startup", fatal = true, "{error:#}");
            return Err(error.context("spawn app-server"));
        }
    };
    timings.record("child_spawned");
    let client = Arc::new(client);
    let (ready_tx, ready_rx) = mpsc::channel::<StartupEvent>(1);
    // The trust gate's answer, awaited by the handshake before it opens a session.
    let (trust_tx, trust_rx) = mpsc::channel::<String>(1);
    app.trust.tx = Some(trust_tx);
    tokio::spawn(handshake(
        client.clone(),
        ready_tx,
        app.session.cwd.clone(),
        show_unready_config,
        app.session.startup_resume.clone(),
        interactive_agent_config,
        trust_rx,
    ));
    let (config_tx, config_rx) = mpsc::channel::<config::Loaded>(1);
    // Theme committed by a `config/write` response, applied on the main thread.
    let (theme_tx, theme_rx) = mpsc::channel::<theme_picker::Event>(1);
    app.theme_picker.tx = Some(theme_tx);
    // Runtime returned by the model picker's `config/write`, applied on the main thread.
    let (model_tx, model_rx) = mpsc::channel::<model_picker::Event>(1);
    app.model_picker.tx = Some(model_tx);
    // Log-level `config/write` answer, applied on the main thread.
    let (log_level_tx, log_level_rx) = mpsc::channel::<log_level_picker::Event>(1);
    app.log_level_picker.tx = Some(log_level_tx);
    // Thinking-level `config/write` answer, applied on the main thread.
    let (thinking_tx, thinking_rx) = mpsc::channel::<thinking_picker::Event>(1);
    app.thinking_picker.tx = Some(thinking_tx);
    // Runtime returned by a Shift+Tab `session/agent/update`, applied on the main thread.
    let (agents_tx, agents_rx) = mpsc::channel::<agents::Event>(1);
    app.agents.tx = Some(agents_tx);
    let (resume_tx, resume_rx) = mpsc::channel::<resume_picker::Event>(8);
    app.resume_picker.tx = Some(resume_tx);
    // Rewind reads and rewinds, applied on the main thread.
    let (rewind_tx, rewind_rx) = mpsc::channel::<rewind::Event>(8);
    app.rewind.tx = Some(rewind_tx);
    // MCP reads, refreshes and toggles, applied on the main thread.
    let (mcp_tx, mcp_rx) = mpsc::channel::<mcp::Event>(8);
    app.mcp.tx = Some(mcp_tx);
    // `mcp/login` answers of the OAuth bottom-app, applied on the main thread.
    let (mcp_oauth_tx, mcp_oauth_rx) = mpsc::channel::<mcp_oauth::Event>(8);
    app.mcp_oauth.tx = Some(mcp_oauth_tx);
    // Connector auth-read and refresh answers, applied on the main thread.
    let (connector_auth_tx, connector_auth_rx) = mpsc::channel::<connector_auth::Event>(8);
    app.connector_auth.tx = Some(connector_auth_tx);
    let (command_tx, command_rx) = mpsc::channel::<CommandEvent>(COMMAND_CHANNEL_CAP);
    app.command_tx = Some(command_tx);
    // Prompt-queue answers (enqueue accepted or rejected), applied on the main thread.
    let (queue_tx, queue_rx) = mpsc::channel::<QueueEvent>(QUEUE_CHANNEL_CAP);
    app.queue.tx = Some(queue_tx);
    let (approval_tx, approval_rx) = mpsc::channel::<approval::Event>(APPROVAL_CHANNEL_CAP);
    app.approval.tx = Some(approval_tx);
    let (feedback_tx, feedback_rx) = mpsc::channel::<feedback::Event>(FEEDBACK_CHANNEL_CAP);
    app.feedback.tx = Some(feedback_tx);
    // Narrator summarize answers, applied on the main thread.
    let (narrator_tx, narrator_rx) = mpsc::channel::<turn_summary::Event>(NARRATOR_CHANNEL_CAP);
    app.narrator.tx = Some(narrator_tx);

    let (paste_image_tx, paste_image_rx) =
        mpsc::channel::<vibe_rs::paste_image::Event>(PASTE_IMAGE_CHANNEL_CAP);
    app.paste_image.tx = Some(paste_image_tx);

    // Recording pipeline -> UI updates (transcript text, notices, state).
    let (voice_tx, voice_rx) = mpsc::channel::<VoiceEvent>(VOICE_CHANNEL_CAP);
    app.voice.tx = Some(voice_tx);

    let sources = event_loop::EventSources {
        notifications,
        ready: ready_rx,
        config: config_rx,
        theme: theme_rx,
        model: model_rx,
        log_level: log_level_rx,
        thinking: thinking_rx,
        agents: agents_rx,
        resume: resume_rx,
        rewind: rewind_rx,
        mcp: mcp_rx,
        mcp_oauth: mcp_oauth_rx,
        connector_auth: connector_auth_rx,
        commands: command_rx,
        queue: queue_rx,
        approval: approval_rx,
        feedback: feedback_rx,
        paste_image: paste_image_rx,
        narrator: narrator_rx,
        voice: voice_rx,
        files: file_changes,
    };

    let (result, summary) = event_loop::EventLoop {
        terminal,
        terminal_guard,
        app,
        client,
        config_tx,
        sources,
        input: InputThread::spawn(),
        crash_rx,
        shutdown,
    }
    .run()
    .await;

    // History persists off-thread; drain anything still pending before exit.
    history_flush.flush();

    // Graceful child shutdown: session/stop was sent in run(); wait then reap.
    // TerminalGuard was dropped inside run() before the server wait, so the
    // terminal is already restored. The server's own exit code after a stop is
    // not reportable: its close() cancels the readline serve task, so it always
    // exits non-zero even on a clean stop. The Python CLI runs the engine
    // in-process and prints nothing on quit; a live-session crash is surfaced
    // by crash_rx instead.
    match child.wait_with_grace().await {
        Some(status) => tracing::debug!("app-server reaped: {status}"),
        None => tracing::debug!("app-server SIGKILLed after grace"),
    }
    timings.flush();
    if let Err(error) = &result {
        // Only ERROR crosses the Sentry bridge, so WARN keeps the fault in the
        // log without reporting a terminal that simply went away.
        if observability::sentry::is_environmental(error) {
            tracing::warn!(vibe_boundary = "event_loop", "{error}");
        } else {
            tracing::error!(vibe_boundary = "event_loop", fatal = true, "{error}");
        }
    }
    // A fatal error takes the red `Error:` path in `main` with exit code 1;
    // a clean quit prints the resume block on the restored terminal.
    result?;
    session_exit::print_session_resume_message(summary.as_ref());
    // Python prints the resume block in the `try` and flushes in the
    // `finally`, so the summary is never delayed by the bounded flush.
    observability::sentry::flush();
    Ok(std::process::ExitCode::SUCCESS)
}
