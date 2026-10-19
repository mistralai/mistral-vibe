//! Filtered Crossterm events with bounded backpressure.

use std::sync::mpsc as std_mpsc;
use std::time::{Duration, Instant};

use crossterm::event::Event;
use tokio::sync::{mpsc, oneshot};

use crate::key_burst::KeyBurst;
use crate::mouse::pixels;
use crate::program_status::detection::Detection;
use crate::terminal_input_filter::TerminalInputFilter;
use crate::terminal_read;

const CHANNEL_CAP: usize = 256;
/// Bounds how late the reader notices a pause, a closed channel, or an expired report.
const POLL_INTERVAL: Duration = Duration::from_millis(50);
const FULL_RETRY_INTERVAL: Duration = Duration::from_millis(5);

/// Pauses terminal reading while a foreground child, such as an editor, owns the terminal.
pub struct Reader {
    pauses: std_mpsc::Sender<Pause>,
    pub program_status: Detection,
}

struct Pause {
    stopped: oneshot::Sender<()>,
    resume: std_mpsc::Receiver<()>,
}

/// Keeps the reader paused; dropping it resumes reading.
pub struct Paused {
    _resume: std_mpsc::Sender<()>,
}

impl Reader {
    /// Stop reading the terminal; returns once the reader thread no longer touches it.
    pub async fn pause(&self) -> Paused {
        let (stopped, stopped_rx) = oneshot::channel();
        let (resume_tx, resume) = std_mpsc::channel();
        if self.pauses.send(Pause { stopped, resume }).is_ok() {
            let _ = stopped_rx.await;
        }
        Paused { _resume: resume_tx }
    }
}

pub fn spawn() -> (mpsc::Receiver<Event>, Reader) {
    let (tx, rx) = mpsc::channel(CHANNEL_CAP);
    let (pauses_tx, pauses) = std_mpsc::channel();
    let program_status = Detection::default();
    let deadline = program_status.query();
    let detection = program_status.clone();
    std::thread::spawn(move || read_terminal(&tx, &pauses, detection, deadline));
    (
        rx,
        Reader {
            pauses: pauses_tx,
            program_status,
        },
    )
}

/// Forward terminal events until the receiver closes or input ends.
fn read_terminal(
    tx: &mpsc::Sender<Event>,
    pauses: &std_mpsc::Receiver<Pause>,
    detection: Detection,
    deadline: Option<Instant>,
) {
    let mut filter = TerminalInputFilter::default();
    let mut bursts = KeyBurst::default();
    let mut coalesce = pixels::Coalesce::default();
    while !tx.is_closed() {
        if let Ok(pause) = pauses.try_recv() {
            serve(pause);
            continue;
        }
        let timeout = bursts.deadline().map_or(POLL_INTERVAL, |deadline| {
            deadline.saturating_duration_since(Instant::now())
        });
        let mut pending = match crossterm::event::poll(timeout) {
            // Leave the event queued for whoever reads the terminal after the TUI.
            Ok(true) if tx.is_closed() => return,
            Ok(true) => match terminal_read::read() {
                Ok(event) => filter.push(event),
                Err(error) => {
                    tracing::warn!(%error, "terminal event read failed");
                    return;
                }
            },
            Ok(false) => Vec::new(),
            Err(error) => {
                tracing::warn!(%error, "terminal event poll failed");
                return;
            }
        };
        detection.observe(&mut filter, deadline);
        let now = Instant::now();
        pending.extend(filter.flush_expired(now));
        // Windows consoles never bracket pastes: they arrive as raw key presses.
        if cfg!(windows) {
            pending = bursts.push(pending, now);
        }
        for event in pending {
            if !coalesce.keep(pixels::grid(), &event) {
                continue;
            }
            if !deliver(tx, pauses, event) {
                return;
            }
        }
    }
}

/// Hold the reader off the terminal until the pause guard drops.
fn serve(pause: Pause) {
    let _ = pause.stopped.send(());
    let _ = pause.resume.recv();
}

/// Wait for room in the channel, still answering pauses: the loop that drains it may be pausing.
fn deliver(tx: &mpsc::Sender<Event>, pauses: &std_mpsc::Receiver<Pause>, event: Event) -> bool {
    let mut event = event;
    loop {
        match tx.try_send(event) {
            Ok(()) => return true,
            Err(mpsc::error::TrySendError::Closed(_)) => return false,
            Err(mpsc::error::TrySendError::Full(back)) => event = back,
        }
        match pauses.try_recv() {
            Ok(pause) => serve(pause),
            Err(_) => std::thread::sleep(FULL_RETRY_INTERVAL),
        }
    }
}
