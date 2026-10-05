//! The onboarding wizard as its own pre-engine loop (Python `run_onboarding`):
//! the app-server has no session yet — the wizard's writes talk `setup/*`
//! on the booted connection, which `--setup` boots behind the welcome
//! screen's gate. The app only lends its view (banner animation, mouse
//! regions, link hitmap).

use anyhow::Result;
use crossterm::event::{Event, KeyCode, KeyEventKind};
use std::sync::Arc;
use tokio::sync::mpsc;

use crate::app::App;
use crate::server::signal::ShutdownSignal;
use crate::server::Client;

use super::actions::{handle_onboarding_action, Close};
use super::boot::{Boot, SetupBoot};
use super::mouse;
use super::screens;
use super::{Action, OnboardingState, Screen};
use crate::event_loop::keys::{InputOutcome, LoopState};
use crate::event_loop::{emit_idle_marker, HOLD_KEY, REDRAW_INTERVAL, RELEASE_KEY};
use crate::setup::auth::browser_sign_in::SignInHandle;

/// No session, no notifications: keys, the welcome animation, the browser
/// sign-in flow, and redraws with the harness idle markers. The wizard's
/// writes talk `setup/*` on the booted connection; `--setup` boots it
/// behind the welcome screen, seeding the wizard when it lands (the gate
/// holds the theme transition until it does). Returns how the wizard
/// closed (`Cancelled` also covers the shutdown signal, like Python's
/// KeyboardInterrupt exit).
pub async fn run_wizard(
    terminal: &mut crate::terminal::Tui,
    app: &mut App,
    wizard: &mut OnboardingState,
    input: &mut mpsc::Receiver<Event>,
    shutdown: &mut ShutdownSignal,
    boot: Boot,
) -> Result<Close> {
    let replaying = std::env::var_os("VIBE_REPLAY_FIXTURE").is_some();
    let mut state = LoopState::new();
    // The wizard's first frame is the startup event the harness settles on.
    state.redraw_pending = true;
    state.real_event_pending = true;
    let (mut client, mut boot_rx) = match boot {
        Boot::Ready { client, status } => {
            super::seed(wizard, &status);
            (Some(client), None)
        }
        Boot::Pending(boot) => (None, Some(boot)),
    };
    wizard.boot_pending = client.is_none();
    let mut welcome_tick = tick(std::time::Duration::from_millis(40));
    let mut redraw_tick = tick(REDRAW_INTERVAL);
    // Python's onboarding `PetitChat` animates on every screen that shows it.
    let mut chat_tick = tick(crate::ui::banner::petit_chat::FRAME_INTERVAL);
    let mut sign_in_rx: Option<SignInHandle> = None;
    loop {
        tokio::select! {
            biased;
            _ = shutdown.wait() => return Ok(Close::Cancelled),
            setup = async { boot_rx.as_mut().expect("armed").await }, if boot_rx.is_some() => {
                boot_rx = None;
                match setup {
                    Ok(SetupBoot::Ready { client: booted, status }) => {
                        super::seed(wizard, &status);
                        client = Some(booted);
                        wizard.boot_pending = false;
                        state.redraw_pending = true;
                        // Replay settles on the frame after the seed, so
                        // the harness's first key meets the seeded wizard.
                        state.real_event_pending = true;
                    }
                    // The loud error path: main's `?` drops the TerminalGuard, restoring the terminal.
                    Ok(SetupBoot::Spawn(error)) => return Err(error),
                    Ok(failure) => {
                        super::close(wizard);
                        return Ok(Close::BootFailed(failure));
                    }
                    // Every boot path sends before completing; a dropped sender would gate Enter forever.
                    Err(_) => {}
                }
            }
            _ = welcome_tick.tick(), if wizard.open
                && wizard.screen == Screen::Welcome
                && !wizard.welcome_done =>
            {
                super::advance_welcome_typing(wizard, replaying);
                state.redraw_pending = true;
            }
            // Replay keeps the cat on its first frame so captures stay stable.
            _ = chat_tick.tick() => {
                if !replaying && app.view.banner.tick(std::time::Instant::now()) {
                    state.redraw_pending = true;
                }
            }
            Some(event) = input.recv() => {
                if replaying
                    && matches!(
                        event,
                        Event::Key(key)
                            if key.code == KeyCode::F(HOLD_KEY) || key.code == KeyCode::F(RELEASE_KEY)
                    )
                {
                    let held = matches!(event, Event::Key(key) if key.code == KeyCode::F(HOLD_KEY));
                    state.apply_input(InputOutcome::Marker(held));
                } else if let Event::Mouse(mouse) = event {
                    // Python's wizard ignores pointer movement entirely: a
                    // move neither repaints nor tracks hover.
                    if mouse.kind != crossterm::event::MouseEventKind::Moved {
                        // The wizard runs before the app-server, so mouse
                        // events route through the region map but dispatch
                        // to the wizard's own client-free handlers.
                        if let Some(target) = crate::mouse::route(app, mouse) {
                            mouse::handle_mouse(app, wizard, mouse, target);
                        }
                        // A preview-scrollbar drag writes its live position
                        // through the shared capture; copy it back so it
                        // stays put when the drag ends.
                        if let Some(position) = crate::mouse::drag_scroll(
                            app,
                            crate::mouse::MouseTarget::OnboardingPreview,
                        ) {
                            wizard.preview_scroll = position;
                        }
                        let down = matches!(
                            mouse.kind,
                            crossterm::event::MouseEventKind::Down(
                                crossterm::event::MouseButton::Left
                            )
                        );
                        state.apply_input(InputOutcome::Activity { reset_blink: down });
                    }
                } else if let Event::Paste(text) = event {
                    // Python's Textual inputs paste; the wizard's inputs are
                    // the same single-line edit model.
                    screens::paste(wizard, &text);
                    state.apply_input(InputOutcome::Activity { reset_blink: false });
                } else if let Some(close) =
                    wizard_key(wizard, &mut sign_in_rx, client.as_ref(), event).await
                {
                    return Ok(close);
                } else {
                    state.apply_input(InputOutcome::Activity { reset_blink: false });
                }
            }
            Some(event) = async {
                match &mut sign_in_rx {
                    Some(handle) => handle.recv().await,
                    None => std::future::pending().await,
                }
            }, if wizard.open && sign_in_rx.is_some() => {
                for action in super::sign_in::sign_in_actions(&mut wizard.browser_sign_in, event)
                {
                    // A sign-in flow only exists past the welcome screen,
                    // which the boot's gate holds: the client is booted.
                    let client = client.as_ref().expect("the sign-in flow is past the gate");
                    if let Some(close) =
                        handle_onboarding_action(wizard, &mut sign_in_rx, client, action).await
                    {
                        return Ok(close);
                    }
                }
                state.apply_input(InputOutcome::Activity { reset_blink: false });
            }
            _ = redraw_tick.tick(), if state.redraw_pending => {
                draw(terminal, app, wizard)?;
                // The wizard parks startup on the user, so its frame is
                // final: every real event owes the replay marker one emit.
                // `--setup`'s pending boot holds the first emit too, so
                // the harness's first key meets the seeded wizard.
                if replaying
                    && state.real_event_pending
                    && !state.marker_held
                    && !state.idle_marker_emitted
                    && client.is_some()
                {
                    emit_idle_marker();
                    state.idle_marker_emitted = true;
                }
                state.redraw_pending = false;
                state.real_event_pending = false;
                continue;
            }
        }
    }
}

/// One wizard key: the wizard action's close, or nothing when it does not
/// act. Pre-boot (`--setup`'s welcome gate holds the theme transition), a
/// key can only cancel — no browser sign-in can be running yet.
async fn wizard_key(
    wizard: &mut OnboardingState,
    sign_in_rx: &mut Option<SignInHandle>,
    client: Option<&Arc<Client>>,
    event: Event,
) -> Option<Close> {
    let Event::Key(key) = event else {
        return None;
    };
    if key.kind != KeyEventKind::Press {
        return None;
    }
    if super::holds_welcome_enter(client.is_none(), wizard, &key) {
        return None;
    }
    let action = screens::handle_key(wizard, key)?;
    match client {
        Some(client) => handle_onboarding_action(wizard, sign_in_rx, client, action).await,
        // Pre-boot only Cancel can act: the welcome gate holds Enter.
        None if matches!(action, Action::Cancel) => {
            super::close(wizard);
            Some(Close::Cancelled)
        }
        None => None,
    }
}

/// Paint the wizard's frame, synchronized like the loop's draws: the wizard
/// owns the whole screen, so the shared mouse regions rebuild around it.
fn draw(
    terminal: &mut crate::terminal::Tui,
    app: &mut App,
    wizard: &mut OnboardingState,
) -> Result<()> {
    use crossterm::SynchronizedUpdate;
    std::io::stdout().sync_update(|_| {
        terminal.draw(|f| {
            app.chat_input.normalize_positions();
            app.view.mouse_regions.clear();
            crate::mouse::register_region(app, f.area(), crate::mouse::MouseTarget::Blocked);
            app.view.link_hitmap.clear();
            super::paint::draw(app, wizard, f, f.area());
        })
    })??;
    crate::pointer::sync(app);
    crate::terminal_notifier::flush(&mut app.terminal_notifier);
    Ok(())
}

/// A skipping, drift-free interval like every loop timer.
fn tick(every: std::time::Duration) -> tokio::time::Interval {
    let mut interval = tokio::time::interval(every);
    interval.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
    interval
}
