//! The row markers and what they mean: one source for the list and its legend.

use ratatui::style::Color;

use super::super::theme;
use crate::app::App;
use crate::sessions_page::Session;

/// A row's state, by priority: the first that applies is the one shown, and a folder shows its first.
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub(super) enum State {
    OpenElsewhere,
    WaitingForYou,
    Working,
    Failed,
    NewReply,
    NothingNew,
}

impl State {
    /// Every state, in the order the legend lists them: the most common first.
    pub(super) const ALL: [State; 6] = [
        State::NothingNew,
        State::NewReply,
        State::OpenElsewhere,
        State::Working,
        State::WaitingForYou,
        State::Failed,
    ];

    pub(super) fn of(app: &App, session: &Session, current: bool) -> Self {
        if app.sessions_page.locked.contains(&session.id) {
            return Self::OpenElsewhere;
        }
        if current && (app.approval.open || app.question_app.open) {
            return Self::WaitingForYou;
        }
        if current && !crate::sessions_page::idle(app) {
            return Self::Working;
        }
        if session.status == "failed" {
            return Self::Failed;
        }
        if session.is_unseen && !current {
            return Self::NewReply;
        }
        Self::NothingNew
    }

    /// One family of circles: hollow is nothing to see, filled is something
    /// to read, and each special state has its own variant.
    pub(super) fn glyph(self) -> &'static str {
        match self {
            Self::OpenElsewhere => "◐",
            Self::WaitingForYou => "◎",
            Self::Working => "◉",
            Self::Failed => "⊗",
            Self::NewReply => "●",
            Self::NothingNew => "○",
        }
    }

    pub(super) fn color(self) -> Color {
        match self {
            Self::OpenElsewhere | Self::WaitingForYou => theme::warning(),
            // Green, like a running mode label: unlike `accent`, it stands
            // apart from primary, warning and error in every theme.
            Self::Working => theme::success(),
            Self::NewReply => theme::primary(),
            Self::Failed => theme::error(),
            Self::NothingNew => theme::muted(),
        }
    }

    /// What the marker means, for the legend and the details pane.
    pub(super) fn label(self) -> &'static str {
        match self {
            Self::OpenElsewhere => "open in another Vibe",
            Self::WaitingForYou => "waiting for you",
            Self::Working => "working",
            Self::Failed => "failed",
            Self::NewReply => "new reply",
            Self::NothingNew => "nothing new",
        }
    }
}
