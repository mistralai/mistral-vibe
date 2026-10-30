//! Session titles and focus-aware terminal notifications.

use std::time::{Duration, Instant};

use crate::program_status::{encode_report, ProgramState};

mod lifecycle;
pub use lifecycle::{action_required, program_state, take_program_report, take_title, title};

#[derive(Clone, Copy)]
pub enum NotificationContext {
    ActionRequired,
    Complete,
    Failed,
}

impl NotificationContext {
    pub fn program_state(self) -> ProgramState {
        match self {
            Self::ActionRequired => {
                ProgramState::Blocked(crate::program_status::BlockedKind::Permission)
            }
            Self::Complete => ProgramState::Done,
            Self::Failed => ProgramState::Error,
        }
    }
}

pub enum Outcome {
    Done,
    Error(String),
}

pub struct TerminalNotifier {
    pub program_status_detection: crate::program_status::detection::Detection,
    protocol_presentation: bool,
    default_title: String,
    outcome: Option<Outcome>,
    last_title: Option<String>,
    report_enabled: bool,
    last_report: Option<String>,
    resend_report: bool,
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
            program_status_detection: Default::default(),
            protocol_presentation: false,
            default_title: "Vibe".to_owned(),
            outcome: None,
            last_title: None,
            report_enabled: false,
            last_report: None,
            resend_report: false,
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

    pub fn clear_outcome(&mut self) {
        self.outcome = None;
        self.bell_context = None;
    }

    pub fn set_focus(&mut self, focused: bool) {
        self.has_focus = focused;
        if focused {
            self.bell_context = None;
        }
        self.dirty = true;
    }

    pub fn set_error_message(&mut self, message: &str) {
        self.outcome = Some(Outcome::Error(
            message
                .lines()
                .next()
                .filter(|line| !line.is_empty())
                .unwrap_or("Task failed")
                .to_owned(),
        ));
    }

    pub fn remember_completion(&mut self, state: ProgramState) {
        match state {
            ProgramState::Done => self.outcome = Some(Outcome::Done),
            ProgramState::Error if !matches!(self.outcome, Some(Outcome::Error(_))) => {
                self.set_error_message("Task failed");
            }
            ProgramState::Idle => self.clear_outcome(),
            _ => {}
        }
    }

    pub fn enable_program_reports(&mut self, enabled: bool) {
        self.report_enabled = enabled;
    }

    pub fn take_program_report(
        &mut self,
        state: ProgramState,
        blocked_message: &str,
    ) -> Option<String> {
        if !self.title_enabled || !self.report_enabled {
            self.last_report.take()?;
            return Some(encode_report(ProgramState::Idle, ""));
        }
        let message = match state {
            ProgramState::Working | ProgramState::Done => &self.default_title,
            ProgramState::Error => match &self.outcome {
                Some(Outcome::Error(message)) => message.as_str(),
                _ => "Task failed",
            },
            ProgramState::Blocked(_) => blocked_message,
            ProgramState::Idle => "",
        };
        let report = encode_report(state, message);
        if !std::mem::take(&mut self.resend_report) && self.last_report.as_ref() == Some(&report) {
            return None;
        }
        self.last_report = Some(report.clone());
        Some(report)
    }

    pub fn invalidate_program_status(&mut self) {
        self.resend_report = true;
    }

    pub fn take_shutdown_report(&mut self) -> Option<String> {
        self.last_report
            .take()
            .map(|_| encode_report(ProgramState::Idle, ""))
    }

    pub fn notify(&mut self, context: NotificationContext, now: Instant) {
        if !matches!(context, NotificationContext::ActionRequired) {
            self.remember_completion(context.program_state());
        }
        self.dirty = true;
        if !self.enabled || self.has_focus || self.use_protocol_presentation() {
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

    pub fn title(&self, state: ProgramState) -> String {
        let title = if self.use_protocol_presentation() {
            self.default_title.clone()
        } else if let Some(context) = self.bell_context.filter(|_| !self.has_focus) {
            let suffix = match context {
                NotificationContext::ActionRequired => "Action Required",
                NotificationContext::Complete | NotificationContext::Failed => "Task Complete",
            };
            format!("{} - {suffix}", self.default_title)
        } else if self.title_enabled {
            match state {
                ProgramState::Working => format!(">> {}", self.default_title),
                ProgramState::Blocked(_) => format!("? {}", self.default_title),
                _ => self.default_title.clone(),
            }
        } else {
            self.default_title.clone()
        };
        title.chars().filter(|ch| !ch.is_control()).collect()
    }

    pub fn invalidate_title(&mut self) {
        self.dirty = true;
    }

    pub fn take_title(&mut self, state: ProgramState) -> Option<String> {
        let protocol = self.use_protocol_presentation();
        self.dirty |= self.protocol_presentation != protocol;
        if protocol && !self.protocol_presentation {
            self.bell_context = None;
            self.bell_pending = false;
        }
        self.protocol_presentation = protocol;
        let title = self.title(state);
        let dirty = std::mem::take(&mut self.dirty);
        if !dirty && self.last_title.as_ref() == Some(&title) {
            return None;
        }
        self.last_title = Some(title.clone());
        Some(title)
    }

    pub fn presentation_changed(&self) -> bool {
        self.protocol_presentation != self.use_protocol_presentation()
    }

    pub fn take_bell(&mut self) -> bool {
        std::mem::take(&mut self.bell_pending) && !self.use_protocol_presentation()
    }

    fn use_protocol_presentation(&self) -> bool {
        self.report_enabled && self.title_enabled && self.program_status_detection.supported()
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
    let config = crate::server::RuntimeSnapshot::config(runtime).unwrap_or_default();
    app.terminal_notifier.configure(
        config.enable_notifications,
        config.experimental_enable_tab_status,
    );
}

pub fn flush(app: &mut crate::app::App) {
    use std::io::Write;
    let title = take_title(app);
    let report = take_program_report(app);
    let bell = app.terminal_notifier.take_bell();
    if !bell && title.is_none() && report.is_none() {
        return;
    }
    let mut out = std::io::stdout();
    if bell {
        let _ = out.write_all(b"\x07");
    }
    if let Some(title) = title {
        let _ = crossterm::execute!(out, crossterm::terminal::SetTitle(title));
    }
    if let Some(report) = report {
        let _ = out.write_all(report.as_bytes());
    }
    let _ = out.flush();
}

pub fn shutdown(notifier: &mut TerminalNotifier) {
    use std::io::Write;
    if let Some(report) = notifier.take_shutdown_report() {
        let mut out = std::io::stdout();
        let _ = out.write_all(report.as_bytes());
        let _ = out.flush();
    }
}
