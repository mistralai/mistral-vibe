//! Session titles and focus-aware terminal notifications, matching Textual.

use std::time::{Duration, Instant};

#[derive(Clone, Copy)]
pub enum NotificationContext {
    ActionRequired,
    Complete,
}

#[derive(Default)]
enum TabState {
    #[default]
    Idle,
    Running,
    Waiting,
}

pub struct TerminalNotifier {
    default_title: String,
    state: TabState,
    has_focus: bool,
    enabled: bool,
    title_enabled: bool,
    bell_context: Option<NotificationContext>,
    last_notification_time: Option<Instant>,
    bell_pending: bool,
    dirty: bool,
}

impl Default for TerminalNotifier {
    fn default() -> Self {
        Self {
            default_title: "Vibe".to_owned(),
            state: TabState::Idle,
            has_focus: true,
            enabled: false,
            title_enabled: false,
            bell_context: None,
            last_notification_time: None,
            bell_pending: false,
            dirty: true,
        }
    }
}

impl TerminalNotifier {
    pub fn configure(&mut self, enabled: bool, title_enabled: bool) {
        self.dirty |= self.enabled != enabled || self.title_enabled != title_enabled;
        self.enabled = enabled;
        self.title_enabled = title_enabled;
    }

    pub fn set_default_title(&mut self, title: &str) {
        let title =
            title.trim_matches(|ch: char| ch.is_whitespace() || matches!(ch, '\u{1c}'..='\u{1f}'));
        let title = if title.is_empty() { "Vibe" } else { title };
        if self.default_title != title {
            self.default_title = title.to_owned();
            self.dirty = true;
        }
    }

    pub fn set_running(&mut self, active: bool) {
        self.state = if active {
            TabState::Running
        } else {
            TabState::Idle
        };
        self.bell_context = None;
        self.dirty = true;
    }

    pub fn set_focus(&mut self, focused: bool) {
        self.has_focus = focused;
        if focused {
            self.bell_context = None;
        }
        self.dirty = true;
    }

    pub fn notify(&mut self, context: NotificationContext, now: Instant) {
        self.state = match context {
            NotificationContext::ActionRequired => TabState::Waiting,
            NotificationContext::Complete => TabState::Idle,
        };
        self.dirty = true;
        if !self.enabled || self.has_focus {
            return;
        }
        self.bell_context = Some(context);
        if self
            .last_notification_time
            .is_none_or(|last| now.duration_since(last) >= Duration::from_secs(1))
        {
            self.last_notification_time = Some(now);
            self.bell_pending = true;
        }
    }

    pub fn title(&self) -> String {
        let title = if let Some(context) = self.bell_context.filter(|_| !self.has_focus) {
            let suffix = match context {
                NotificationContext::ActionRequired => "Action Required",
                NotificationContext::Complete => "Task Complete",
            };
            format!("{} - {suffix}", self.default_title)
        } else if self.title_enabled {
            match self.state {
                TabState::Running => format!(">> {}", self.default_title),
                TabState::Waiting => format!("? {}", self.default_title),
                TabState::Idle => self.default_title.clone(),
            }
        } else {
            self.default_title.clone()
        };
        title.chars().filter(|ch| !ch.is_control()).collect()
    }

    pub fn invalidate_title(&mut self) {
        self.dirty = true;
    }

    pub fn take_title(&mut self) -> Option<String> {
        std::mem::take(&mut self.dirty).then(|| self.title())
    }

    pub fn take_bell(&mut self) -> bool {
        std::mem::take(&mut self.bell_pending)
    }
}

pub fn updated_title(params: &serde_json::Value) -> Option<&str> {
    let patch = params.get("patch")?.as_array()?;
    let operation = patch.iter().rev().find(|operation| {
        operation["path"] == "/title"
            && matches!(operation["op"].as_str(), Some("add" | "replace" | "remove"))
    })?;
    if operation["op"] == "remove" {
        return None;
    }
    operation["value"].as_str()
}

pub fn configure(app: &mut crate::app::App, runtime: &serde_json::Value) {
    let config = &runtime["runtime"]["config"];
    app.terminal_notifier.configure(
        config["enableNotifications"].as_bool().unwrap_or(true),
        config["experimentalEnableTabStatus"]
            .as_bool()
            .unwrap_or(true),
    );
}

pub fn action_required(app: &mut crate::app::App) {
    app.terminal_notifier
        .notify(NotificationContext::ActionRequired, Instant::now());
}

pub fn restore_running(app: &mut crate::app::App) {
    let running = matches!(app.session.status, crate::app::Status::Generating { .. })
        || app.session.shell_operation_id.is_some();
    app.terminal_notifier.set_running(running);
}

pub fn flush(notifier: &mut TerminalNotifier) {
    use std::io::Write;
    let bell = notifier.take_bell();
    let title = notifier.take_title();
    if !bell && title.is_none() {
        return;
    }
    let mut out = std::io::stdout();
    if bell {
        let _ = out.write_all(b"\x07");
    }
    if let Some(title) = title {
        let _ = crossterm::execute!(out, crossterm::terminal::SetTitle(title));
    }
    let _ = out.flush();
}
