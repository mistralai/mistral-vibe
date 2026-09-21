//! Dedicated OS thread forwarding Crossterm events (Party 1 of the A3 runtime).

use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::thread::JoinHandle;
use std::time::{Duration, Instant};

use crossterm::event::{self, Event};
use tokio::sync::mpsc;

/// Bounded event channel capacity; the input thread blocks on send at this bound.
pub const EVENT_CHANNEL_CAP: usize = 256;

/// Input-thread poll interval; the stop flag is re-checked after each timeout.
const POLL_INTERVAL: Duration = Duration::from_millis(100);

/// Owns the blocking-poll reader thread and its receiver.
pub struct InputThread {
    pub rx: mpsc::Receiver<Event>,
    stop: Arc<AtomicBool>,
    join: Option<JoinHandle<()>>,
}

impl InputThread {
    /// Spawn the reader thread forwarding events over a bounded channel.
    pub fn spawn() -> Self {
        let (tx, rx) = mpsc::channel::<Event>(EVENT_CHANNEL_CAP);
        let stop = Arc::new(AtomicBool::new(false));
        let stop_thread = stop.clone();
        let join = std::thread::Builder::new()
            .name("vibe-input".into())
            .spawn(move || input_loop(tx, stop_thread))
            .expect("spawn input thread");
        Self {
            rx,
            stop,
            join: Some(join),
        }
    }

    /// Stop the thread, close the receiver, and join. Idempotent.
    pub fn shutdown(&mut self) {
        self.stop.store(true, Ordering::Relaxed);
        self.rx.close();
        if let Some(handle) = self.join.take() {
            let _ = handle.join();
        }
    }
}

impl Drop for InputThread {
    fn drop(&mut self) {
        self.shutdown();
    }
}

fn input_loop(tx: mpsc::Sender<Event>, stop: Arc<AtomicBool>) {
    let mut filter = crate::terminal_input_filter::TerminalInputFilter::default();
    while !stop.load(Ordering::Relaxed) {
        let events = match event::poll(POLL_INTERVAL) {
            Ok(true) => match event::read() {
                Ok(event) => filter.push(event),
                Err(err) => {
                    tracing::warn!(%err, "input read error; stopping input thread");
                    break;
                }
            },
            Ok(false) => filter.flush_expired(Instant::now()),
            Err(err) => {
                tracing::warn!(%err, "input poll error; stopping input thread");
                break;
            }
        };
        for event in events {
            if tx.blocking_send(event).is_err() {
                return;
            }
        }
    }
}
