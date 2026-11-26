//! Requests awaiting a response; closed for good once the app-server is gone.

use std::collections::HashMap;
use std::sync::{Arc, Mutex, MutexGuard};

use serde_json::Value;
use tokio::sync::oneshot;

use super::process::RequestFailure;

type Reply = Result<Value, RequestFailure>;
type Waiters = Option<HashMap<u64, oneshot::Sender<Reply>>>;

/// `None` once closed: a request can never wait on an app-server that is gone.
#[derive(Clone)]
pub struct Pending(Arc<Mutex<Waiters>>);

impl Default for Pending {
    fn default() -> Self {
        Self(Arc::new(Mutex::new(Some(HashMap::new()))))
    }
}

impl Pending {
    /// The reply channel for `id`; already failed when the app-server is closed.
    pub fn register(&self, id: u64) -> oneshot::Receiver<Reply> {
        let (tx, rx) = oneshot::channel();
        match self.lock().as_mut() {
            Some(waiters) => {
                waiters.insert(id, tx);
            }
            None => {
                let _ = tx.send(Err(RequestFailure::AppServerClosed));
            }
        }
        rx
    }

    pub fn take(&self, id: u64) -> Option<oneshot::Sender<Reply>> {
        self.lock().as_mut()?.remove(&id)
    }

    /// Fail every waiting request and refuse new ones.
    pub fn close(&self) {
        for (_, tx) in self.lock().take().into_iter().flatten() {
            let _ = tx.send(Err(RequestFailure::AppServerClosed));
        }
    }

    pub fn is_empty(&self) -> bool {
        self.lock().as_ref().is_none_or(HashMap::is_empty)
    }

    fn lock(&self) -> MutexGuard<'_, Waiters> {
        self.0.lock().unwrap_or_else(|err| err.into_inner())
    }
}
