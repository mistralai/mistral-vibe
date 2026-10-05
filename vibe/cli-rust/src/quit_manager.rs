//! Double-press quit confirmation (Python `QuitManager`).

use std::time::{Duration, Instant};

/// Window in which a second press of the same key confirms quit.
pub const QUIT_CONFIRM_DELAY: Duration = Duration::from_secs(1);

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum QuitConfirmKey {
    CtrlC,
    CtrlD,
}

impl QuitConfirmKey {
    pub fn label(self) -> &'static str {
        match self {
            Self::CtrlC => "Ctrl+C",
            Self::CtrlD => "Ctrl+D",
        }
    }
}

#[derive(Clone, Debug)]
pub struct QuitPending {
    pub key: QuitConfirmKey,
    pub at: Instant,
    /// Queue warning frozen when quit was armed, empty when nothing is queued.
    pub extra: String,
}

pub struct QuitManager {
    pending: Option<QuitPending>,
    /// Python `ask_confirmation_on_exit`; only gates Ctrl+D.
    pub ask_confirmation_on_exit: bool,
}

impl Default for QuitManager {
    fn default() -> Self {
        Self {
            pending: None,
            ask_confirmation_on_exit: true,
        }
    }
}

impl QuitManager {
    /// The armed confirmation, while its window is still open.
    pub fn active(&self) -> Option<&QuitPending> {
        self.pending
            .as_ref()
            .filter(|pending| pending.at.elapsed() < QUIT_CONFIRM_DELAY)
    }

    pub fn is_confirmed(&self, key: QuitConfirmKey) -> bool {
        self.active().is_some_and(|pending| pending.key == key)
    }

    pub fn request_confirmation(&mut self, key: QuitConfirmKey, queued: usize) {
        self.pending = Some(QuitPending {
            key,
            at: Instant::now(),
            extra: quit_warning_extra(queued),
        });
    }

    pub fn cancel_confirmation(&mut self) {
        self.pending = None;
    }
}

/// Python `QueueController.quit_warning_extra`.
pub fn quit_warning_extra(queued: usize) -> String {
    match queued {
        0 => String::new(),
        1 => "1 queued message will be discarded".into(),
        n => format!("{n} queued messages will be discarded"),
    }
}
