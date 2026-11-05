//! Client work in flight, so the replay harness acks a fence only once every result has landed.

use std::future::Future;
use std::pin::Pin;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::task::{Context, Poll};

use tokio::sync::Notify;
use tokio::task::JoinHandle;

static IN_FLIGHT: AtomicUsize = AtomicUsize::new(0);
static FINISHED: Notify = Notify::const_new();

/// Keeps the client busy until dropped.
pub struct Work(());

impl Work {
    pub fn start() -> Self {
        IN_FLIGHT.fetch_add(1, Ordering::SeqCst);
        Self(())
    }
}

impl Drop for Work {
    fn drop(&mut self) {
        if IN_FLIGHT.fetch_sub(1, Ordering::SeqCst) == 1 {
            FINISHED.notify_one();
        }
    }
}

/// Whether no tracked work is in flight.
pub fn idle() -> bool {
    IN_FLIGHT.load(Ordering::SeqCst) == 0
}

/// Resolves once the last tracked work has ended.
pub async fn finished() {
    FINISHED.notified().await;
}

/// Spawn a task that hands its result to the event loop before it ends; the client stays busy until then.
#[allow(clippy::disallowed_methods)]
pub fn spawn<F>(future: F) -> JoinHandle<F::Output>
where
    F: Future + Send + 'static,
    F::Output: Send + 'static,
{
    tokio::spawn(Tracked {
        future: Box::pin(future),
        _work: Work::start(),
    })
}

/// Spawn a long-lived task (a pump, a poll loop, a playback) that never holds the client busy.
#[allow(clippy::disallowed_methods)]
pub fn spawn_daemon<F>(future: F) -> JoinHandle<F::Output>
where
    F: Future + Send + 'static,
    F::Output: Send + 'static,
{
    tokio::spawn(future)
}

/// Run blocking `f` on the blocking pool; the client stays busy until it returns.
#[allow(clippy::disallowed_methods)]
pub fn spawn_blocking<F, R>(f: F) -> JoinHandle<R>
where
    F: FnOnce() -> R + Send + 'static,
    R: Send + 'static,
{
    let work = Work::start();
    tokio::task::spawn_blocking(move || {
        let _work = work;
        f()
    })
}

/// The future drops before its guard, so cleanup in its drop counts as work too.
struct Tracked<F> {
    future: Pin<Box<F>>,
    _work: Work,
}

impl<F: Future> Future for Tracked<F> {
    type Output = F::Output;

    fn poll(mut self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<F::Output> {
        self.future.as_mut().poll(cx)
    }
}
