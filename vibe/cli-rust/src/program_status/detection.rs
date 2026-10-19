//! Nonblocking OSC 7501 support query.

use std::io::Write;
use std::sync::{
    atomic::{AtomicBool, Ordering},
    Arc,
};
use std::time::{Duration, Instant};

use crate::terminal_input_filter::TerminalInputFilter;

const REPLY_TIMEOUT: Duration = Duration::from_millis(500);

#[derive(Clone, Default)]
pub struct Detection(Arc<AtomicBool>);

impl Detection {
    pub fn supported(&self) -> bool {
        self.0.load(Ordering::Relaxed)
    }

    pub fn query(&self) -> Option<Instant> {
        if !super::supported() {
            return None;
        }
        let mut out = std::io::stdout().lock();
        out.write_all(b"\x1b]7501;?\x1b\\").ok()?;
        out.flush().ok()?;
        Some(Instant::now() + REPLY_TIMEOUT)
    }

    pub fn observe(&self, filter: &mut TerminalInputFilter, deadline: Option<Instant>) {
        if filter.take_program_status_reply() && deadline.is_some_and(|at| Instant::now() <= at) {
            self.0.store(true, Ordering::Relaxed);
        }
    }
}
