//! The onboarding wizard as its own pre-engine loop:
//! the app-server has no session yet — the wizard's writes talk `setup/*`
//! on the booted connection, which `--setup` boots behind the welcome
//! screen's gate. The app only lends its view (banner animation, mouse
//! regions, link hitmap).

use anyhow::Result;
use crossterm::event::{Event, KeyEventKind};
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
use crate::event_loop::REDRAW_INTERVAL;
use crate::setup::auth::browser_sign_in::SignInHandle;

/// No session, no notifications: keys, the welcome animation, the browser
/// sign-in flow, and redraws that ack the replay harness's fences. The wizard's
/// writes talk `setup/*` on the booted connection; `--setup` boots it
/// behind the welcome screen, seeding the wizard when it lands (the gate
/// holds the theme transition until it does). Returns how the wizard
/// closed (`Cancelled` also covers the shutdown signal).
pub async fn run_wizard(
    terminal: &mut crate::terminal::Tui,
    app: &mut App,
    wizard: &mut OnboardingState,
    input: &mut mpsc::Receiver<Event>,
    shutdown: &mut ShutdownSignal,
    boot: Boot,
) -> Result<Close> {
    let replaying = crate::utils::is_replaying();
    let mut state = LoopState::new();
    state.redraw_pending = true;
    let (mut client, mut boot_rx) = match boot {
        Boot::Ready { client, status } => {
            super::seed(wizard, &status);
            (Some(client), None)
        }
        Boot::Pending(boot) => (None, Some(boot)),
    };
    wizard.boot_pending = client.is_none();
    let mut welcome_tick = crate::clock::interval_real(std::time::Duration::from_millis(40));
    let mut redraw_tick = crate::clock::interval_real(REDRAW_INTERVAL);
    // The onboarding petit chat animates on every screen that shows it, on UI time like every animation.
    let mut chat_tick = crate::clock::interval(crate::ui::banner::petit_chat::FRAME_INTERVAL);
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
            _ = chat_tick.tick() => {
                if app.view.banner.tick(crate::clock::now()) {
                    state.redraw_pending = true;
                }
            }
            // The success dwell is UI time: it runs before the next key's fence, so a scenario advances past it.
            _ = crate::clock::sleep_until(wizard.browser_sign_in.finished_at()), if wizard.open => {
                let (_, action) = wizard.browser_sign_in.finished.take().expect("armed");
                let client = client.as_ref().expect("the sign-in flow is past the gate");
                if let Some(close) =
                    handle_onboarding_action(wizard, &mut sign_in_rx, client, action).await
                {
                    return Ok(close);
                }
                state.redraw_pending = true;
            }
            Some(event) = input.recv() => {
                if matches!(event, Event::Resize(..)) {
                    let _ = crate::terminal::sync_pixel_reports();
                }
                if crate::replay_fence::observe(&event) {
                    state.redraw_pending = true;
                } else if let Event::Mouse(mouse) = event {
                    mouse::end_dropped_gesture(app, &event);
                    let (mouse, row) = crate::mouse::pixels::to_cells(mouse);
                    // The wizard ignores pointer movement entirely: a move
                    // neither repaints nor tracks hover.
                    if mouse.kind != crossterm::event::MouseEventKind::Moved {
                        // The wizard runs before the app-server, so mouse
                        // events route through the region map but dispatch
                        // to the wizard's own client-free handlers.
                        if let Some(target) = crate::mouse::route_at(app, mouse, row) {
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
                        state.apply_input(InputOutcome::Redraw { reset_blink: down });
                    }
                } else if let Event::Paste(text) = event {
                    // Paste goes to the focused single-line input.
                    screens::paste(wizard, &text);
                    state.apply_input(InputOutcome::Redraw { reset_blink: false });
                } else {
                    mouse::end_dropped_gesture(app, &event);
                    if let Some(close) =
                        wizard_key(wizard, &mut sign_in_rx, client.as_ref(), event).await
                    {
                        return Ok(close);
                    }
                    state.apply_input(InputOutcome::Redraw { reset_blink: false });
                }
                // Cell reports are held exactly while a gesture is captured.
                crate::terminal::hold_cell_reports(app.view.mouse.holds_target());
            }
            Some(event) = async {
                match &mut sign_in_rx {
                    Some(handle) => handle.recv().await,
                    None => std::future::pending().await,
                }
            }, if wizard.open && sign_in_rx.is_some() => {
                super::sign_in::apply_sign_in_event(&mut wizard.browser_sign_in, event);
                state.redraw_pending = true;
            }
            // The last tracked task may end without sending anything: re-check the ack.
            _ = crate::work::finished(), if replaying => state.redraw_pending = true,
            _ = redraw_tick.tick(), if state.redraw_pending => {
                draw(terminal, app, wizard)?;
                // Results ride tracked work or the sign-in channel (read idle first); `--setup`'s pending boot holds the ack.
                crate::replay_fence::ack_if(|| {
                    crate::work::idle()
                        && sign_in_rx.as_ref().is_none_or(SignInHandle::is_empty)
                        && client.is_some()
                });
                state.redraw_pending = false;
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
            crate::terminal_hyperlinks::mark(
                app,
                f.buffer_mut(),
                crate::mouse::MouseTarget::OnboardingLinks,
            );
        })
    })??;
    crate::pointer::sync(app);
    crate::terminal_notifier::flush(app);
    Ok(())
}
