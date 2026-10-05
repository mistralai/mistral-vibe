//! vibe-rs: a render-perf PoC TUI for Vibe over the app-server JSON-RPC protocol.

use anyhow::{Context, Result};
use clap::Parser;
use std::path::PathBuf;
use std::sync::Arc;
use tokio::sync::mpsc;

use vibe_rs::app::App;
use vibe_rs::cli::{rewrite_update_argv, workdir_to_string, Cli};
use vibe_rs::headless;
use vibe_rs::headless_prompt;
use vibe_rs::observability::logging;
use vibe_rs::startup::{launch_from_env, StartupEvent, StartupRecorder};
use vibe_rs::update_flow;
use vibe_rs::worktree_gate;
use vibe_rs::{agents, event_loop, observability, session_exit, setup, terminal, ui, utils};

#[tokio::main(flavor = "current_thread")]
async fn main() -> std::process::ExitCode {
    match run().await {
        Ok(code) => code,
        Err(error) => {
            session_exit::print_error(&error.to_string());
            vibe_rs::rollout::print_fallback_hint();
            // Python flushes in a `finally` on every exit path, after the
            // error itself is reported.
            observability::sentry::flush();
            std::process::ExitCode::from(1)
        }
    }
}

async fn run() -> Result<std::process::ExitCode> {
    vibe_rs::rollout::install_panic_hint();
    let mut timings = StartupRecorder::new();
    timings.record("process_entry");
    // clap's parse_from treats the first element as the program name, so hand
    // it the whole argv — the rewrite looks at the element after the program.
    let cli = Cli::parse_from(rewrite_update_argv(std::env::args_os().collect()));
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

    // Load ~/.vibe/.env into the client's own environment (Python
    // `load_dotenv_values`, `cli.py:92`). The app-server child loads the file
    // itself: the dotenv-set keys are stripped from its env in
    // `Client::spawn`, so python-dotenv — not this parser — decides them.
    vibe_rs::credentials::dotenv::load_dotenv_values();

    if let Some(vibe_rs::cli::CliCommand::Mcp { command }) = &cli.command {
        return Ok(vibe_rs::mcp_command::run(command, launch).await);
    }

    // Python `run_cli`: the forced check runs after the workdir switch and
    // before anything interactive (and without the pre-ready Sentry init).
    if cli.check_upgrade {
        return Ok(update_flow::run_check_upgrade().await);
    }

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
        // Python prints the worktree lines in programmatic mode too
        // (`_enter_worktree` runs before the mode split); "Using worktree"
        // follows inside `headless::run`, once the settle lands.
        worktree_gate::print_preparing(options.worktree.as_ref());
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
    // Bare `--worktree` names the worktree from the initial prompt (positional
    // or piped stdin), which is already read at this point. Python skips the
    // worktree preparation for `--setup`: the wizard runs and the process
    // exits, so there is no session to move into a worktree.
    let worktree_request = if cli.setup {
        None
    } else {
        cli.worktree_request(cli.interactive_initial_prompt(stdin_prompt.clone()))
    };
    let interactive_agent_config =
        cli.interactive_agent_config(Some(cwd_text.clone()), worktree_request)?;

    let cached_startup_config = utils::startup_cache::StartupConfig::load();
    let show_unready_config = cached_startup_config.is_none();
    let startup_config = cached_startup_config.unwrap_or_default();
    let resolved_auto = vibe_rs::theme_detection::resolve_auto_theme();
    ui::theme::prepare_active(&startup_config.theme, resolved_auto);

    // Python prepares the worktree before the TUI exists (`_enter_worktree`):
    // parity means waiting the server's preparation out right here, with the
    // same two stderr lines, and letting the terminal start only once the
    // session has moved. The preparation itself stays server-side (ADR 0016);
    // this only sequences the startup around it.
    let worktree_run = interactive_agent_config.worktree.is_some();
    // A worktree run spawns the app-server before the wait below, so the
    // handlers go in first: a Ctrl-C during `Preparing worktree...` tears
    // the child down instead of dying on the default SIGINT path.
    let mut shutdown = if worktree_run {
        Some(vibe_rs::server::signal::install().context("install signal handlers")?)
    } else {
        None
    };
    let early = match shutdown.as_mut() {
        Some(signals) => match worktree_gate::early_start(
            launch.clone(),
            cwd_text.clone(),
            show_unready_config,
            startup_resume.clone(),
            interactive_agent_config.clone(),
            signals,
        )
        .await
        {
            Ok(start) => Some(start),
            Err(error) => {
                // Python's top level turns this window's KeyboardInterrupt
                // into a dim `Bye!` and exit 0; the child is dropped (and its
                // process group killed) inside `early_start`.
                if error
                    .downcast_ref::<worktree_gate::PrepInterrupt>()
                    .is_some()
                {
                    vibe_rs::worktree_exit::print_dim("\nBye!");
                    observability::sentry::flush();
                    return Ok(std::process::ExitCode::SUCCESS);
                }
                return Err(error);
            }
        },
        None => None,
    };
    // Python `entrypoint.py` enters the worktree before `run_cli` shows the
    // startup update prompt; parity means the dialog runs after the settle
    // and before the terminal is taken over. A non-worktree run settles
    // nothing, so its prompt timing is unchanged.
    update_flow::maybe_run_startup_update_prompt(
        &startup_config,
        early.as_ref().map(|start| &start.child),
    )
    .await;
    // Install before the TUI owns the terminal, so a failure exits cleanly.
    let mut shutdown = match shutdown {
        Some(signals) => signals,
        None => vibe_rs::server::signal::install().context("install signal handlers")?,
    };
    let (mut terminal, mut terminal_guard) = terminal::init()?;
    timings.record("terminal_ready");

    // Python chdirs the whole process into the worktree, so its `@`
    // completions follow the move; the gate's settle is that same moment, and
    // the index watches the worktree from the first frame. A settle that
    // never came (deadline, old server) keeps watching the startup cwd.
    let index_root = early
        .as_ref()
        .and_then(|start| {
            start.startup.iter().find_map(|event| match event {
                StartupEvent::Ready(ready) => ready.settled.as_deref().map(PathBuf::from),
                _ => None,
            })
        })
        .or_else(|| cwd.clone());
    let (files, file_changes) = utils::file_index::FileIndex::start(index_root);
    let cached_tokens = (0, startup_config.context_window);
    let mut app = App::default();
    // Hold the positional prompt until startup converges (Python `_initial_prompt`).
    // Python falls back to piped stdin: `initial_prompt or stdin_prompt`, where an
    // empty positional (`vibe ""`) is falsy and yields to the piped prompt.
    app.session.initial_prompt = cli.interactive_initial_prompt(stdin_prompt);
    app.session.teleport_on_start = cli.teleport;
    // Python keeps one `_session_options` for the process and resends it on every resume.
    app.session.agent_config = interactive_agent_config.clone();
    app.chat_input.history = utils::history_manager::HistoryManager::load();
    let history_flush = app.chat_input.history.flush_handle();
    // `--setup` runs workspace-less like Python's (whose wizard exits before
    // any server session exists), and no session is ever opened, so the
    // trust gate never fires for it either.
    app.session.cwd = Some(cwd_text.clone());
    app.session.startup_resume = startup_resume;
    app.completion.files = files;
    app.completion.skills = startup_config.skills.clone();
    agents::show_startup_agents(&mut app, &startup_config);
    app.session.startup_config = startup_config;
    app.session.tokens = cached_tokens;
    app.subagents.main_tokens = cached_tokens;
    // Python `require_api_key_or_onboarding` meets ADR 0016's draw-first
    // rule: a keyed launch goes straight to the session handshake, and only
    // a keyless one asks the pre-session `setup/status` — whose missing-key
    // answer opens the wizard on the SAME child (one child per round,
    // server-owned key persistence). A worktree run's verdict sits in the
    // replay buffer; the wizard round it opens spawns its own child.
    let mut input = vibe_rs::terminal_events::spawn();
    // `--setup` never renders the chat frame: the wizard owns the first
    // paint and holds every choice behind the overlapped boot's welcome
    // gate, then prints and exits without a session, so the run ends here.
    if cli.setup {
        let code = setup::rounds::setup_round(
            &mut terminal,
            &mut app,
            &mut input,
            &mut shutdown,
            &launch,
            &mut timings,
        )
        .await?;
        drop(terminal_guard);
        terminal::release(terminal);
        setup::exit::exit_flushes(&history_flush, &timings);
        return Ok(code);
    }
    vibe_rs::event_handler::announce_resume(&mut app);
    vibe_rs::event_handler::show_dangerous_directory_warning(&mut app);
    // A worktree run spawned the client pre-TUI and drove the handshake
    // while the gate waited; everything it absorbed is replayed after the
    // channels exist, below. A normal launch spawns behind the drawn frame
    // and hands the boot the ready channel's sender; a worktree run skips
    // the probe (its early arm never reads `keyed`, so the keyring read is
    // skipped).
    let keyed = !worktree_run && vibe_rs::credentials::default_key_present();
    // The draw-first overlap (ADR 0016), gated on the probe: a keyed normal
    // launch paints the unready frame before the child exists, and the
    // spawn, the boot, and the handshake converge behind it. A
    // probe-negative launch never reaches the loop before its first paint:
    // the wizard's pre-loop round owns the first frame and hands the booted
    // child back (below). A worktree run keeps the gate's sequencing.
    if !worktree_run && keyed {
        terminal.draw(|frame| app.draw(frame))?;
        timings.record("first_draw");
        vibe_rs::startup::mark_first_draw();
    }

    let mut retry_version: Option<Option<String>> = None;
    let (client, child, notifications, crash_rx, ready_tx, ready_rx, trust_tx, mut replay) =
        match early {
            Some(early) => {
                // The gate's handshake consumed its ready channel's sender;
                // the event loop needs a live pair for the missing-key
                // wizard's post-persist retry.
                let (ready_tx, ready_rx) = mpsc::channel::<StartupEvent>(1);
                drop(early.ready);
                (
                    early.client,
                    early.child,
                    early.notifications,
                    early.crash_rx,
                    ready_tx,
                    ready_rx,
                    Some(early.trust_tx),
                    Some(early.startup),
                )
            }
            None if !keyed => {
                // Python `require_api_key_or_onboarding`: a keyless run's
                // wizard paints instantly (Python's needs no server); the
                // boot overlaps behind the welcome screen, and the round
                // hands the same booted child back for the handshake's
                // retry on it (below).
                match setup::preloop::round(
                    &mut terminal,
                    &mut app,
                    &mut input,
                    &mut shutdown,
                    &launch,
                    &mut timings,
                )
                .await?
                {
                    setup::preloop::PreloopEnd::Exit(code) => {
                        drop(terminal_guard);
                        terminal::release(terminal);
                        setup::exit::exit_flushes(&history_flush, &timings);
                        return Ok(code);
                    }
                    setup::preloop::PreloopEnd::Booted(booted) => {
                        // The round's continue-anyway warnings are owed on
                        // the real terminal before the loop repaints.
                        if let Some(message) = booted.warning {
                            setup::exit::print_pre_tui_warning(
                                &message,
                                &mut terminal,
                                &mut terminal_guard,
                            )?;
                        }
                        retry_version = Some(booted.server_version);
                        let (ready_tx, ready_rx) = mpsc::channel::<StartupEvent>(1);
                        (
                            booted.client,
                            booted.child,
                            booted.notifications,
                            booted.crash_rx,
                            ready_tx,
                            ready_rx,
                            None,
                            None,
                        )
                    }
                }
            }
            None => {
                let (client, child, notifications, crash_rx) =
                    match vibe_rs::server::Client::spawn(launch.clone()).await {
                        Ok(spawned) => spawned,
                        Err(error) => {
                            // The Sentry bridge only carries ERROR records,
                            // so the startup window's own failure sites log
                            // their own fatal errors.
                            tracing::error!(vibe_boundary = "startup", fatal = true, "{error:#}");
                            return Err(error.context("spawn app-server"));
                        }
                    };
                timings.record("child_spawned");
                let client = Arc::new(client);
                // One boot task owns the ready channel end to end: it
                // initializes the connection and runs the session
                // handshake on the same child (the probe already said the
                // key is present, so no pre-session status is asked).
                let (ready_tx, ready_rx) = mpsc::channel::<StartupEvent>(1);
                let (trust_tx, trust_rx) = mpsc::channel::<String>(1);
                tokio::spawn(setup::boot::background_boot(
                    client.clone(),
                    ready_tx.clone(),
                    trust_rx,
                    vibe_rs::startup::HandshakeParams {
                        cwd: app.session.cwd.clone(),
                        show_unready_config,
                        resume: app.session.startup_resume.clone(),
                        agent_config: interactive_agent_config.clone(),
                        notifications: None,
                        pre_initialized: None,
                        trust_already_resolved: false,
                    },
                ));
                (
                    client,
                    child,
                    notifications,
                    crash_rx,
                    ready_tx,
                    ready_rx,
                    Some(trust_tx),
                    None,
                )
            }
        };
    app.trust.tx = trust_tx;
    let channels = vibe_rs::channels::connect(&mut app);
    let config_tx = channels.config_tx.clone();

    let sources = channels.sources(notifications, ready_rx, file_changes);
    let mut run = event_loop::EventLoop {
        terminal,
        terminal_guard,
        app,
        client,
        child: Some(child),
        launch,
        config_tx,
        ready_tx,
        show_unready_config,
        sources,
        input,
        crash_rx,
        shutdown,
    };
    if let Some(version) = retry_version.take() {
        // The pre-loop round's child is initialized and already plumbed:
        // the handshake retries on it (the server's own env mutation is
        // what made a persisted key visible) and the chat frame paints
        // over the wizard's last frame.
        // The pre-loop round's retry is this child's first handshake: the
        // trust gate still has to run.
        setup::retry_handshake(&mut run, None, version, false);
        run = setup::rounds::paint_relaunch(run, &mut timings);
    }

    // The replay fold and the live ready stream raise the same missing-key
    // verdict; the rounds driver runs the pre-session wizard and re-enters
    // the loop on the round's child — never an in-loop overlay.
    let (result, _summary) =
        match setup::rounds::run_rounds(run, replay.take(), &history_flush, &mut timings).await? {
            setup::rounds::RoundsOutcome::Finished { result, summary } => (result, summary),
            setup::rounds::RoundsOutcome::ExitSuccess => {
                return Ok(std::process::ExitCode::SUCCESS)
            }
            setup::rounds::RoundsOutcome::ExitFailure => {
                return Ok(std::process::ExitCode::from(1))
            }
        };

    // History persists off-thread; drain anything still pending before exit.
    history_flush.flush();

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
    // a clean quit already printed the resume block inside the event loop,
    // before the worktree cleanup, matching Python's exit ordering.
    result?;
    // Python prints the resume block in the `try` and flushes in the
    // `finally`, so the summary is never delayed by the bounded flush.
    observability::sentry::flush();
    Ok(std::process::ExitCode::SUCCESS)
}
