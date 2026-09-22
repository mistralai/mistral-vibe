use std::fmt;

/// Why the Core refused to produce a transition.
///
/// The variants answer different questions for the caller:
///
/// - `InvalidState` — the command type is known, but it is not legal in the
///   current idle, running, or compacting state.
/// - `InvalidCorrelation` — an action result does not name an action currently
///   pending in Core.
/// - `InvalidCommand` — the command is not valid for the current state or
///   carries malformed input. The Core made no state change; the Runtime should
///   reject the command and keep the session.
/// - `InvalidConfiguration` — a create or reconfigure payload failed validation.
///   Also a caller error, but about configuration rather than a turn, and it
///   names the offending field.
/// - `Invariant` — a Core invariant was violated. This is a Core bug, not client
///   input, and should be triaged as such rather than blamed on the caller.
///
/// The Session Protocol preserves `InvalidState` and `InvalidCorrelation` as
/// typed rejections. The remaining variants render as `invalid_command` today.
///
/// There is deliberately no "which command" tag: `HarnessSession` already knows
/// the command it is applying and reports it as `command_type`, so carrying it
/// here would duplicate state that cannot disagree.
#[derive(Clone, Debug, PartialEq, Eq)]
pub(crate) enum CoreError {
    InvalidState {
        state: crate::core::step_protocol::CommandState,
    },
    InvalidCorrelation {
        received_action_id: String,
        pending_action_ids: Vec<String>,
    },
    InvalidCommand {
        detail: String,
    },
    InvalidConfiguration {
        field: &'static str,
        detail: String,
    },
    Invariant {
        detail: String,
    },
}

impl CoreError {
    pub(crate) fn invalid_state(state: crate::core::step_protocol::CommandState) -> Self {
        Self::InvalidState { state }
    }

    pub(crate) fn invalid_correlation(
        received_action_id: impl Into<String>,
        pending_action_ids: Vec<String>,
    ) -> Self {
        Self::InvalidCorrelation {
            received_action_id: received_action_id.into(),
            pending_action_ids,
        }
    }

    pub(crate) fn invalid_command(detail: impl Into<String>) -> Self {
        Self::InvalidCommand {
            detail: detail.into(),
        }
    }

    pub(crate) fn invalid_configuration(field: &'static str, detail: impl Into<String>) -> Self {
        Self::InvalidConfiguration {
            field,
            detail: detail.into(),
        }
    }

    pub(crate) fn invariant(detail: impl Into<String>) -> Self {
        Self::Invariant {
            detail: detail.into(),
        }
    }

    /// The client-facing sentence. Never includes the `field` tag, so the
    /// Session Protocol wire message is unchanged.
    pub(crate) fn detail(&self) -> &str {
        match self {
            Self::InvalidState { .. } => "command is invalid in the current session state",
            Self::InvalidCorrelation { .. } => "command does not correlate with a pending action",
            Self::InvalidCommand { detail }
            | Self::InvalidConfiguration { detail, .. }
            | Self::Invariant { detail } => detail,
        }
    }
}

impl fmt::Display for CoreError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(self.detail())
    }
}
